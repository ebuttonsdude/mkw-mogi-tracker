"""Native packed NVFP4 execution for the isolated Qwen worker.

Ported from the measured FlashInfer CUTE backend. Weight packing and dynamic
activation quantization stay unchanged; no BF16 dequantization fallback exists.
The supported backend requires NVIDIA SM120/121 and CUDA 13 or newer.
"""

from collections import Counter
from dataclasses import replace
import importlib

NVFP4_KERNEL_REVISION = "d9b0c745a36a5506240fa059b97d0222bf66b644"


class FlashInferNVFP4Adapter:
    """Replace GEMM without changing weight storage or quantization numerics."""

    def __init__(self, kernel, quantize_op, mm_fp4, torch_module, *,
                 backend="cute-dsl", preserve_tiny_gemv=True):
        if backend not in ("cute-dsl", "b12x"):
            raise ValueError("Choose explicit cute-dsl or b12x; automatic fallback is forbidden")
        self.kernel = kernel
        self.quantize_op = quantize_op
        self.mm_fp4 = mm_fp4
        self.torch = torch_module
        self.backend = backend
        self.preserve_tiny_gemv = preserve_tiny_gemv
        self.calls = Counter()
        self.shapes = Counter()
        self._integration = None
        self._original_loader = None
        self._replacement_loader = None

    def _scales(self, value):
        if value.dtype == self.torch.int32:
            return value.view(self.torch.float8_e4m3fn)
        if value.dtype not in (self.torch.float8_e4m3fn, self.torch.uint8):
            raise ValueError("NVFP4 scales must be packed int32 or E4M3 bytes")
        return value

    def gemm(self, pw, x):
        if x.shape[-1] != pw.k:
            raise ValueError(f"Expected {pw.k} input features, got {x.shape[-1]}")
        x2 = x.reshape(-1, pw.k).contiguous()
        m = x2.shape[0]
        shape = f"m={m},n={pw.n},k={pw.k}"
        self.calls["attempted"] += 1
        try:
            if m <= 2 and self.preserve_tiny_gemv and getattr(pw, "sf_rowmajor", None) is not None:
                y = self.kernel.gemm(pw, x)
                self.calls["original_w4a16_gemv"] += 1
                self.shapes[f"original_w4a16_gemv:{shape}"] += 1
                return y
            if pw.n % 64 or pw.k % 64:
                raise ValueError("FlashInfer SM120 NVFP4 requires N and K divisible by 64")
            if pw.qweight.dtype != self.torch.uint8 or tuple(pw.qweight.shape) != (pw.n, pw.k // 2):
                raise ValueError("Expected packed uint8 weight[N,K/2]")
            a_amax = x2.abs().amax().to(self.torch.float32)
            a_gs = (448.0 * 6.0 / a_amax.clamp(min=1e-12)).reshape(1)
            aq, a_sf = self.quantize_op(x2.to(self.torch.bfloat16), a_gs, True)
            alpha = (1.0 / (a_gs * pw.global_scale)).to(self.torch.float32)
            a_sf = self._scales(a_sf)
            b_sf = self._scales(pw.sf)
            if tuple(a_sf.shape) != (((m + 127) // 128) * 128, pw.k // 16):
                raise ValueError("Activation scales do not retain physical 128-row padding")
            if tuple(b_sf.shape) != (((pw.n + 127) // 128) * 128, pw.k // 16):
                raise ValueError("Weight scales do not retain physical 128-row padding")
            # HF stores W as contiguous [N,K/2]. FlashInfer expects its
            # column-major [K/2,N] view, and the corresponding scale transpose.
            y = self.mm_fp4(
                aq.contiguous(), pw.qweight.T, a_sf.contiguous(), b_sf.T,
                alpha=alpha, out_dtype=self.torch.bfloat16, block_size=16,
                use_8x4_sf_layout=False, backend=self.backend, use_nvfp4=True,
                enable_pdl=False,
            )
            self.calls["flashinfer_w4a4_gemm"] += 1
            self.shapes[f"flashinfer_w4a4_gemm:{shape}"] += 1
            return y.reshape(*x.shape[:-1], pw.n)
        except Exception:
            self.calls["failed"] += 1
            raise

    def metadata(self):
        return {
            "backend": f"flashinfer.mm_fp4:{self.backend}",
            "weight_quantization": "original HF NVFP4 pack, unchanged",
            "activation_quantization": "original HF scaled_fp4_quant, dynamic per-tensor global scale",
            "scale_layout": "E4M3 128x4, physically padded M/N; no weight shuffle",
            "fallback": "none; errors propagate",
            "preserve_tiny_w4a16_gemv": self.preserve_tiny_gemv,
            "calls": dict(self.calls), "shapes": dict(self.shapes),
        }

    def restore(self):
        """Restore only this adapter's process-local loader patch."""
        if self._integration is not None and self._integration.load_nvfp4_kernel is self._replacement_loader:
            self._integration.load_nvfp4_kernel = self._original_loader
            self._integration = None


def install_adapter(*, backend="cute-dsl", preserve_tiny_gemv=True):
    """Install process-local adapter, returning its accounting object.

    Imports/loads the real kernel, but does not itself launch GPU operations.
    The caller owns GPU preflight and representative correctness probes.
    """
    import torch
    from flashinfer.gemm import mm_fp4
    import transformers.integrations.nvfp4 as integration

    from kernels import get_kernel

    original_loader = integration.load_nvfp4_kernel
    if getattr(original_loader, "_flashinfer_nvfp4_adapter", None) is not None:
        raise RuntimeError("A FlashInfer NVFP4 adapter is already installed in this process")
    module = get_kernel("kernels-community/nvfp4-gemm", revision=NVFP4_KERNEL_REVISION)
    kernel = integration.NVFP4Kernel(**{
        name: getattr(module, name) for name in ('PackedWeight', 'gemm', 'pack', 'swizzled_sf_shape')})
    original_functional = importlib.import_module(kernel.gemm.__module__)
    adapter = FlashInferNVFP4Adapter(
        kernel, original_functional.ops.scaled_fp4_quant, mm_fp4, torch,
        backend=backend, preserve_tiny_gemv=preserve_tiny_gemv,
    )
    replacement_kernel = replace(kernel, gemm=adapter.gemm)

    def replacement_loader():
        return replacement_kernel

    replacement_loader._flashinfer_nvfp4_adapter = adapter
    adapter._integration = integration
    adapter._original_loader = original_loader
    adapter._replacement_loader = replacement_loader
    integration.load_nvfp4_kernel = replacement_loader
    return adapter
