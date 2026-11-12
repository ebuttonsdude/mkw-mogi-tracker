"""Persistent local GPU transcription worker for the supported Qwen profiles."""

import argparse
import base64
import contextlib
import io
import sys
from pathlib import Path

try:
    from ..name_engines import QWEN_PROFILES
    from .paddle_worker import MAX_IMAGE_BYTES, serve
    from .qwen_kernels import BASE_EXCLUSIONS, excluded_name, install_selective_kernels, nvfp4_exclusions
except ImportError:  # Direct invocation by the isolated worker interpreter.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from mkw_tracker.name_engines import QWEN_PROFILES
    from mkw_tracker.workers.paddle_worker import MAX_IMAGE_BYTES, serve
    from mkw_tracker.workers.qwen_kernels import (
        BASE_EXCLUSIONS, excluded_name, install_selective_kernels, nvfp4_exclusions,
    )


def preflight(torch, profile):
    """Reject unsupported hardware before loading any model weights."""
    if (not torch.cuda.is_available() or getattr(torch.version, 'hip', None)
            or not getattr(torch.version, 'cuda', None)):
        raise RuntimeError('Qwen OCR requires an NVIDIA CUDA GPU; use PaddleOCR or Tesseract on Radeon/CPU')
    if profile['requires_nvfp4']:
        if tuple(torch.cuda.get_device_capability(0)) not in {(12, 0), (12, 1)}:
            raise RuntimeError('This NVFP4 reader requires an NVIDIA Blackwell GPU with NVFP4 support '
                               '(SM120/121, such as RTX 50 series); choose INT8, BF16, PaddleOCR or Tesseract')
        cuda_version = getattr(torch.version, 'cuda', None)
        try:
            cuda_major = int(cuda_version.split('.')[0])
        except (TypeError, ValueError, AttributeError):
            cuda_major = 0
        if cuda_major < 13:
            raise RuntimeError('NVFP4 requires the dedicated CUDA 13+ runtime; run the setup tool for this reader')
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError('This Qwen reader requires BF16-capable NVIDIA hardware; use PaddleOCR or Tesseract')
    free, total = torch.cuda.mem_get_info()
    minimum = profile['min_free_gib']
    if free < minimum * 1024**3:
        raise RuntimeError(f"{profile['label']} needs at least {minimum:g} GiB of free GPU memory "
                           f'with safety headroom; {free / 1024**3:.2f} GiB is free. '
                           'Choose a smaller compatible reader, PaddleOCR or Tesseract')
    # This process alone keeps a two-GiB allocator reserve. CUDA allocations
    # outside PyTorch and other applications remain outside this limit.
    torch.cuda.set_per_process_memory_fraction(min((free - 2 * 1024**3) / total, 1.0), device=0)


class QwenWorker:
    def __init__(self, model_dir, profile='qwen'):
        if profile not in QWEN_PROFILES:
            raise ValueError('Unknown Qwen reader profile')
        self.model_dir = Path(model_dir)
        self.profile_id = profile
        self.profile = QWEN_PROFILES[profile]
        self.model = None
        self.processor = None
        self._adapter = None

    def _load(self, torch, transformers):
        if not self.model_dir.is_dir():
            raise ValueError('Local Qwen model is missing; run the neural OCR setup tool for the selected reader')
        preflight(torch, self.profile)
        processor = transformers.AutoProcessor.from_pretrained(
            self.model_dir, local_files_only=True, trust_remote_code=False,
            min_pixels=3136, max_pixels=262144)
        processor.tokenizer.padding_side = 'left'
        config = transformers.AutoConfig.from_pretrained(
            self.model_dir, local_files_only=True, trust_remote_code=False)
        modern = self.profile['family'] == '35'
        expected_type = 'qwen3_5' if modern else 'qwen3_vl'
        if config.model_type != expected_type:
            raise ValueError(f'Selected reader requires {expected_type}, not {config.model_type}')
        model_class = getattr(transformers, 'Qwen3_5ForConditionalGeneration' if modern
                              else 'Qwen3VLForConditionalGeneration')
        kwargs = {'dtype': torch.bfloat16, 'device_map': {'': 0}, 'attn_implementation': 'sdpa',
                  'local_files_only': True, 'trust_remote_code': False}
        if modern:
            kwargs['use_kernels'] = False
        precision = self.profile['precision']
        exclusions = list(BASE_EXCLUSIONS)
        expected_converted = None
        try:
            if precision == 'int8':
                kwargs['quantization_config'] = transformers.BitsAndBytesConfig(
                    load_in_8bit=True, llm_int8_skip_modules=exclusions)
            elif precision == 'nf4':
                kwargs['quantization_config'] = transformers.BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_quant_type='nf4',
                    bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=False,
                    llm_int8_skip_modules=exclusions)
            elif precision == 'nvfp4':
                exclusions, expected_converted = nvfp4_exclusions(config, model_class, torch)
                from mkw_tracker.workers.qwen_nvfp4 import install_adapter
                self._adapter = install_adapter()
                kwargs['quantization_config'] = transformers.NVFP4Config(modules_to_not_convert=exclusions)
            model = model_class.from_pretrained(self.model_dir, **kwargs).eval()
            if {str(parameter.device) for parameter in model.parameters()} - {'cuda:0'}:
                raise RuntimeError('Qwen reader must stay on CUDA device 0; CPU/disk offload is not supported')
            converted = {name for name, module in model.named_modules()
                         if type(module).__name__ in {'NVFP4Linear', 'Linear8bitLt', 'Linear4bit'}}
            if precision == 'bf16' and converted:
                raise RuntimeError('BF16 reader unexpectedly contains quantized modules')
            if precision == 'int8' and len(converted) != 252:
                raise RuntimeError('INT8 reader did not convert the expected 252 language projections')
            if precision == 'nf4' and len(converted) != 252:
                raise RuntimeError('NF4 reader did not convert the expected 252 language projections')
            if precision == 'nvfp4' and (converted != expected_converted or len(converted) != (400 if modern else 252)):
                raise RuntimeError('NVFP4 reader did not convert the expected language projections')
            if any(excluded_name(name, BASE_EXCLUSIONS) for name in converted):
                raise RuntimeError('Vision encoder or output head was unexpectedly quantized')
            if modern:
                install_selective_kernels(model)
        except Exception:
            if self._adapter is not None:
                self._adapter.restore()
                self._adapter = None
            raise
        self.processor, self.model = processor, model

    def recognize(self, request):
        from PIL import Image

        values = request.get('images') if isinstance(request, dict) else None
        if not isinstance(values, list) or not 1 <= len(values) <= 12:
            raise ValueError('images must contain between 1 and 12 PNG images')
        images = []
        for value in values:
            if not isinstance(value, str) or len(value) > MAX_IMAGE_BYTES * 4 // 3 + 4:
                raise ValueError('Row image exceeds size limit')
            data = base64.b64decode(value, validate=True)
            if len(data) >= MAX_IMAGE_BYTES:
                raise ValueError('Row image exceeds size limit')
            with Image.open(io.BytesIO(data)) as image:
                if image.format != 'PNG' or image.height > 2048 or image.width > 8192:
                    raise ValueError('Expected a bounded PNG row image')
                images.append(image.convert('RGB'))
        with contextlib.redirect_stdout(sys.stderr):
            import torch

            if self.model is None:
                import transformers
                self._load(torch, transformers)
            prompt = ('Transcribe the single player name in this image exactly. It may contain Japanese kana, '
                      'Latin letters, digits, Greek letters, punctuation, and symbols. Preserve case and every '
                      'visible symbol. Return only the name, without explanation or quotation marks. '
                      'Do not translate or normalize the name. Do not add spaces absent from the image.')
            texts = [self.processor.apply_chat_template([{'role': 'user', 'content': [
                {'type': 'image', 'image': image}, {'type': 'text', 'text': prompt}]}],
                tokenize=False, add_generation_prompt=True, enable_thinking=False) for image in images]
            inputs = self.processor(text=texts, images=images, padding=True, return_tensors='pt').to(self.model.device)
            with torch.inference_mode():
                outputs = self.model.generate(**inputs, max_new_tokens=96, do_sample=False, use_cache=True)
            trimmed = [output[len(original):] for original, output in zip(inputs.input_ids, outputs)]
            names = self.processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)
        # Generative transcription has no calibrated character confidence.
        return {'rows': [{'name': name.strip(), 'confidence': 0.0} for name in names],
                'engine': 'qwen3-vl-8b' if self.profile_id == 'qwen' else self.profile_id,
                'confidence_kind': 'unavailable'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', required=True)
    parser.add_argument('--profile', choices=QWEN_PROFILES, default='qwen')
    args = parser.parse_args()
    serve(QwenWorker(args.model_dir, args.profile), sys.stdin, sys.stdout)


if __name__ == '__main__':
    main()
