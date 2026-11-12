"""CPU-only routing tests. No real FlashInfer or HF kernel is imported."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

try:
    import torch
except ImportError:
    torch = None

from mkw_tracker.workers import qwen_nvfp4 as MODULE


@unittest.skipIf(torch is None, "Optional PyTorch is not installed in the core application environment")
class AdapterTensorTests(unittest.TestCase):
    def setUp(self):
        self.weight = SimpleNamespace(
            n=64, k=64, qweight=torch.zeros((64, 32), dtype=torch.uint8),
            sf=torch.zeros((128, 1), dtype=torch.int32),
            global_scale=torch.tensor([2.0]), sf_rowmajor=torch.zeros((64, 4), dtype=torch.uint8))
        self.kernel = SimpleNamespace(gemm=Mock(return_value=torch.ones((1, 64))))

        def quantize(x, scale, swizzled):
            self.assertEqual(x.dtype, torch.bfloat16)
            self.assertTrue(swizzled)
            m = x.shape[0]
            return (torch.zeros((m, 32), dtype=torch.uint8),
                    torch.zeros((((m + 127) // 128) * 128, 1), dtype=torch.int32))

        self.quantize = Mock(side_effect=quantize)
        self.mm = Mock(side_effect=lambda a, b, *scales, **kwargs: torch.zeros((a.shape[0], b.shape[1])))
        self.adapter = MODULE.FlashInferNVFP4Adapter(self.kernel, self.quantize, self.mm, torch)

    def test_ragged_batch_transposes_weights_and_scales_without_dequantization(self):
        x = torch.ones((3, 4, 64))
        out = self.adapter.gemm(self.weight, x)
        self.assertEqual(out.shape, (3, 4, 64))
        a, b, sa, sb = self.mm.call_args.args
        self.assertEqual(a.shape, (12, 32))
        self.assertEqual(b.shape, (32, 64))
        self.assertEqual(b.dtype, torch.uint8)
        self.assertTrue(b.T.is_contiguous())
        self.assertEqual(sa.shape, (128, 4))
        self.assertEqual(sb.shape, (4, 128))
        self.assertTrue(sb.T.is_contiguous())
        self.assertEqual(sa.dtype, torch.float8_e4m3fn)
        self.assertEqual(sb.dtype, torch.float8_e4m3fn)
        self.assertEqual(b.data_ptr(), self.weight.qweight.data_ptr())
        self.assertEqual(sb.data_ptr(), self.weight.sf.data_ptr())
        kw = self.mm.call_args.kwargs
        self.assertEqual(kw['backend'], 'cute-dsl')
        self.assertEqual(kw['block_size'], 16)
        self.assertTrue(kw['use_nvfp4'])
        self.assertFalse(kw['use_8x4_sf_layout'])
        self.assertEqual(kw['out_dtype'], torch.bfloat16)
        self.assertAlmostEqual(kw['alpha'].item(), 1 / (2688 * 2))
        self.kernel.gemm.assert_not_called()
        self.assertEqual(self.adapter.metadata()['calls'], {'attempted': 1, 'flashinfer_w4a4_gemm': 1})

    def test_tiny_rows_preserve_explicitly_counted_original_gemv(self):
        x = torch.ones((1, 64))
        out = self.adapter.gemm(self.weight, x)
        self.assertIs(out, self.kernel.gemm.return_value)
        self.quantize.assert_not_called()
        self.mm.assert_not_called()
        self.assertEqual(self.adapter.calls['original_w4a16_gemv'], 1)

    def test_tiny_rows_can_be_forced_to_real_w4a4(self):
        self.adapter.preserve_tiny_gemv = False
        self.adapter.gemm(self.weight, torch.ones((1, 64)))
        self.mm.assert_called_once()
        self.kernel.gemm.assert_not_called()

    def test_failure_propagates_and_does_not_call_original_gemm(self):
        self.mm.side_effect = RuntimeError('native failure')
        with self.assertRaisesRegex(RuntimeError, 'native failure'):
            self.adapter.gemm(self.weight, torch.ones((12, 64)))
        self.assertEqual(self.adapter.calls['failed'], 1)
        self.assertEqual(self.adapter.calls['flashinfer_w4a4_gemm'], 0)
        self.kernel.gemm.assert_not_called()

    def test_bad_alignment_is_rejected_not_fallen_back(self):
        self.weight.n = 32
        with self.assertRaisesRegex(ValueError, 'divisible by 64'):
            self.adapter.gemm(self.weight, torch.ones((12, 64)))
        self.mm.assert_not_called()
        self.kernel.gemm.assert_not_called()

    def test_input_dimension_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'Expected 64'):
            self.adapter.gemm(self.weight, torch.ones((12, 32)))
        self.quantize.assert_not_called()

    def test_scale_padding_is_required(self):
        self.weight.sf = torch.zeros((64, 1), dtype=torch.int32)
        with self.assertRaisesRegex(ValueError, 'Weight scales'):
            self.adapter.gemm(self.weight, torch.ones((12, 64)))
        self.mm.assert_not_called()

    def test_explicit_backend_required(self):
        with self.assertRaisesRegex(ValueError, 'automatic fallback'):
            MODULE.FlashInferNVFP4Adapter(self.kernel, self.quantize, self.mm, torch, backend='auto')

    def test_metadata_is_json_serializable(self):
        import json
        self.adapter.gemm(self.weight, torch.ones((12, 64)))
        self.assertIn('flashinfer_w4a4_gemm', json.dumps(self.adapter.metadata()))


class AdapterInstallTests(unittest.TestCase):
    def test_loading_is_pinned_patch_is_process_local_and_duplicate_install_is_rejected(self):
        from dataclasses import make_dataclass
        from types import ModuleType
        import sys
        from unittest.mock import patch

        kernel_type = make_dataclass('NVFP4Kernel', ['PackedWeight', 'gemm', 'pack', 'swizzled_sf_shape'], frozen=True)
        functional = ModuleType('test_packed_nvfp4_functional')
        functional.ops = SimpleNamespace(scaled_fp4_quant=Mock())
        def gemm(*args):
            raise AssertionError('Must not execute a GPU operation during installation')
        gemm.__module__ = functional.__name__
        packed = SimpleNamespace(PackedWeight=object, gemm=gemm, pack=Mock(), swizzled_sf_shape=Mock())
        original_loader = Mock(spec=[])
        integration = ModuleType('transformers.integrations.nvfp4')
        integration.NVFP4Kernel = kernel_type
        integration.load_nvfp4_kernel = original_loader
        integrations = ModuleType('transformers.integrations')
        integrations.nvfp4 = integration
        transformers = ModuleType('transformers')
        transformers.integrations = integrations
        flashinfer = ModuleType('flashinfer')
        flashinfer.gemm = ModuleType('flashinfer.gemm')
        flashinfer.gemm.mm_fp4 = Mock()
        kernels = ModuleType('kernels')
        kernels.get_kernel = Mock(return_value=packed)
        modules = {'torch': SimpleNamespace(), 'transformers': transformers,
                   'transformers.integrations': integrations,
                   'transformers.integrations.nvfp4': integration,
                   'flashinfer': flashinfer, 'flashinfer.gemm': flashinfer.gemm,
                   'kernels': kernels, functional.__name__: functional}
        with patch.dict(sys.modules, modules):
            adapter = MODULE.install_adapter()
            kernels.get_kernel.assert_called_once_with('kernels-community/nvfp4-gemm', revision=MODULE.NVFP4_KERNEL_REVISION)
            original_loader.assert_not_called()
            replacement = integration.load_nvfp4_kernel
            self.assertIs(replacement().gemm.__self__, adapter)
            self.assertIs(replacement().pack, packed.pack)
            with self.assertRaisesRegex(RuntimeError, 'already installed'):
                MODULE.install_adapter()
            adapter.restore()
            self.assertIs(integration.load_nvfp4_kernel, original_loader)
            # A stale adapter must not undo a different loader installed later.
            integration.load_nvfp4_kernel = replacement
            adapter.restore()
            self.assertIs(integration.load_nvfp4_kernel, replacement)

    def test_import_does_not_require_optional_runtime(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-c',
                                 'import sys;from mkw_tracker.workers import qwen_nvfp4;'
                                 'assert "torch" not in sys.modules;'
                                 'assert "transformers" not in sys.modules;'
                                 'assert "flashinfer" not in sys.modules'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
