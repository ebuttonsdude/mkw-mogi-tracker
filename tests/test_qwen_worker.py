"""CPU-only tests for profile routing, hardware guards and isolated Qwen loading."""

import base64
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from mkw_tracker.name_engines import QWEN_PROFILES
from mkw_tracker.workers import qwen_kernels, qwen_worker

GIB = 1024**3


def torch_stub(*, free=30*GIB, capability=(12, 0), available=True, hip=None, cuda='13.0', bf16=True):
    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=Mock(return_value=available),
                             get_device_capability=Mock(return_value=capability),
                             is_bf16_supported=Mock(return_value=bf16),
                             mem_get_info=Mock(return_value=(free, 32*GIB)),
                             set_per_process_memory_fraction=Mock()),
        version=SimpleNamespace(hip=hip, cuda=cuda), bfloat16='bfloat16',
        device=lambda device: contextlib.nullcontext(),
        inference_mode=contextlib.nullcontext)


class Linear:
    def __init__(self, inputs=64, outputs=64, bias=False):
        self.in_features, self.out_features = inputs, outputs
        self.bias = object() if bias else None


def model_stack(profile_id, *, device='cuda:0', converted_count=None, wrong_architecture=False):
    profile = QWEN_PROFILES[profile_id]
    modern = profile['family'] == '35'
    count = 400 if modern else 252
    prefix = 'model.language_model.layers'
    shapes = [(f'{prefix}.{index}.mlp.up_proj', Linear()) for index in range(count)]
    shapes += [('model.visual.projection', Linear()), ('lm_head', Linear()),
               (f'{prefix}.0.linear_attn.in_proj_a', Linear(outputs=48)),
               (f'{prefix}.0.linear_attn.in_proj_ba', Linear(outputs=64)),
               ('biased', Linear(bias=True)), ('unaligned', Linear(inputs=80))]
    template = SimpleNamespace(named_modules=lambda: shapes)
    kind = {'bf16': 'Linear', 'int8': 'Linear8bitLt', 'nf4': 'Linear4bit', 'nvfp4': 'NVFP4Linear'}[profile['precision']]
    converted = [(name, type(kind, (), {})()) for name, _ in shapes[:count]]
    if converted_count is not None:
        converted = converted[:converted_count]
    model = SimpleNamespace(device=device, parameters=lambda: [SimpleNamespace(device=device)],
                            named_modules=lambda: converted, generate=Mock(return_value=[[1, 2, 3, 4]]))
    model.eval = Mock(return_value=model)
    model_class = Mock(return_value=template)
    model_class.from_pretrained = Mock(return_value=model)
    config = SimpleNamespace(model_type='wrong' if wrong_architecture else 'qwen3_5' if modern else 'qwen3_vl')
    processor = Mock()
    processor.tokenizer = SimpleNamespace(padding_side='right')
    processor.batch_decode.return_value = ['  ST★avid  ']
    class Inputs(dict):
        input_ids = [[1, 2]]
        def to(self, target):
            return self
    processor.return_value = Inputs(input_ids=[[1, 2]])
    stack = SimpleNamespace(AutoProcessor=SimpleNamespace(from_pretrained=Mock(return_value=processor)),
                            AutoConfig=SimpleNamespace(from_pretrained=Mock(return_value=config)),
                            Qwen3VLForConditionalGeneration=model_class,
                            Qwen3_5ForConditionalGeneration=model_class,
                            BitsAndBytesConfig=Mock(side_effect=lambda **kwargs: kwargs),
                            NVFP4Config=Mock(side_effect=lambda **kwargs: kwargs))
    return stack, model_class, model, processor


class QwenWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model_dir = Path(self.temp.name)
        self.torch = torch_stub()
        self.torch.nn = SimpleNamespace(Linear=Linear)
        self.adapter = Mock()
        patcher = patch('mkw_tracker.workers.qwen_nvfp4.install_adapter', return_value=self.adapter)
        self.install_adapter = patcher.start()
        self.addCleanup(patcher.stop)
        self.kernel_patcher = patch.object(qwen_worker, 'install_selective_kernels')
        self.kernels = self.kernel_patcher.start()
        self.addCleanup(self.kernel_patcher.stop)

    def test_import_and_direct_help_do_not_import_model_stack_or_probe_hardware(self):
        command = 'import sys;from mkw_tracker.workers import qwen_worker;assert "torch" not in sys.modules;assert "transformers" not in sys.modules'
        result = subprocess.run([sys.executable, '-c', command], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        script = Path(qwen_worker.__file__)
        result = subprocess.run([sys.executable, str(script), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('qwen36-nvfp4', result.stdout)

    def test_default_remains_original_bf16(self):
        worker = qwen_worker.QwenWorker(self.model_dir)
        self.assertEqual(worker.profile_id, 'qwen')
        stack, model_class, model, processor = model_stack('qwen')
        worker._load(self.torch, stack)
        self.assertIs(worker.model, model)
        self.assertIs(worker.processor, processor)
        self.assertEqual(processor.tokenizer.padding_side, 'left')
        self.assertNotIn('quantization_config', model_class.from_pretrained.call_args.kwargs)
        self.install_adapter.assert_not_called()
        self.kernels.assert_not_called()

    def test_each_profile_uses_expected_precision_and_all_gpu_loading(self):
        for profile_id, profile in QWEN_PROFILES.items():
            with self.subTest(profile=profile_id):
                stack, model_class, model, _ = model_stack(profile_id)
                worker = qwen_worker.QwenWorker(self.model_dir, profile_id)
                worker._load(self.torch, stack)
                self.assertIs(worker.model, model)
                kw = model_class.from_pretrained.call_args.kwargs
                self.assertEqual(kw['device_map'], {'': 0})
                self.assertTrue(kw['local_files_only'])
                self.assertFalse(kw['trust_remote_code'])
                self.assertEqual(kw['dtype'], 'bfloat16')
                self.assertEqual(kw['attn_implementation'], 'sdpa')
                if profile['precision'] == 'int8':
                    self.assertEqual(kw['quantization_config'],
                                     {'load_in_8bit': True, 'llm_int8_skip_modules': list(qwen_kernels.BASE_EXCLUSIONS)})
                elif profile['precision'] == 'nf4':
                    self.assertEqual(kw['quantization_config'], {
                        'load_in_4bit': True, 'bnb_4bit_quant_type': 'nf4',
                        'bnb_4bit_compute_dtype': 'bfloat16', 'bnb_4bit_use_double_quant': False,
                        'llm_int8_skip_modules': list(qwen_kernels.BASE_EXCLUSIONS)})
                elif profile['precision'] == 'nvfp4':
                    excludes = kw['quantization_config']['modules_to_not_convert']
                    for suffix in ('in_proj_a', 'in_proj_ba'):
                        self.assertTrue(any(name.endswith(suffix) for name in excludes))
                    self.assertIn('biased', excludes)
                    self.assertIn('unaligned', excludes)
                else:
                    self.assertNotIn('quantization_config', kw)
                if profile['family'] == '35':
                    self.assertFalse(kw['use_kernels'])
                    self.kernels.assert_called_with(model)

    def test_free_memory_thresholds_have_independent_profiles_and_safety_reserve(self):
        for profile in QWEN_PROFILES.values():
            minimum = profile['min_free_gib'] * GIB
            with self.subTest(profile=profile['label']):
                torch = torch_stub(free=minimum-1)
                with self.assertRaisesRegex(RuntimeError, 'safety headroom'):
                    qwen_worker.preflight(torch, profile)
                torch.cuda.set_per_process_memory_fraction.assert_not_called()
                torch = torch_stub(free=minimum)
                qwen_worker.preflight(torch, profile)
                torch.cuda.set_per_process_memory_fraction.assert_called_once_with((minimum-2*GIB)/(32*GIB), device=0)

    def test_unsupported_gpu_is_rejected_before_processor_or_weight_loading(self):
        scenarios = [('qwen', torch_stub(available=False), 'NVIDIA CUDA'),
                     ('qwen-int8', torch_stub(hip='7.0'), 'Radeon/CPU'),
                     ('qwen', torch_stub(bf16=False), 'BF16-capable'),
                     ('qwen-nvfp4', torch_stub(capability=(8, 9)), 'Blackwell'),
                     ('qwen-nvfp4', torch_stub(capability=(10, 0)), 'SM120/121'),
                     ('qwen36-nvfp4', torch_stub(cuda='12.8'), 'CUDA 13'),
                     ('qwen36-nvfp4', torch_stub(cuda=None), 'NVIDIA CUDA'),
                     ('qwen-int8', torch_stub(cuda=None), 'NVIDIA CUDA'),
                     ('qwen36-nvfp4', torch_stub(cuda='invalid'), 'CUDA 13')]
        for profile_id, torch, error in scenarios:
            with self.subTest(profile=profile_id, error=error):
                stack, model_class, _, _ = model_stack(profile_id)
                worker = qwen_worker.QwenWorker(self.model_dir, profile_id)
                with self.assertRaisesRegex(RuntimeError, error):
                    worker._load(torch, stack)
                stack.AutoProcessor.from_pretrained.assert_not_called()
                model_class.from_pretrained.assert_not_called()
        self.install_adapter.assert_not_called()

    def test_nf4_accepts_ada_with_bounded_memory_and_no_blackwell_adapter(self):
        profile = QWEN_PROFILES['qwen-4b-nf4']
        self.assertEqual(profile['precision'], 'nf4')
        self.assertEqual(profile['min_free_gib'], 6.5)
        torch = torch_stub(free=int(6.5*GIB), capability=(8, 9), cuda='12.8')
        stack, _, model, _ = model_stack('qwen-4b-nf4')
        worker = qwen_worker.QwenWorker(self.model_dir, 'qwen-4b-nf4')
        worker._load(torch, stack)
        self.assertIs(worker.model, model)
        self.install_adapter.assert_not_called()
        self.kernels.assert_not_called()
        torch.cuda.set_per_process_memory_fraction.assert_called_once_with(4.5/32, device=0)

    def test_nf4_hardware_and_memory_fail_before_model_loading(self):
        for torch, error in ((torch_stub(free=int(6.5*GIB)-1), '6.5 GiB'),
                             (torch_stub(bf16=False), 'BF16-capable'),
                             (torch_stub(hip='7.0'), 'Radeon/CPU')):
            with self.subTest(error=error):
                stack, model_class, _, _ = model_stack('qwen-4b-nf4')
                with self.assertRaisesRegex(RuntimeError, error):
                    qwen_worker.QwenWorker(self.model_dir, 'qwen-4b-nf4')._load(torch, stack)
                stack.AutoProcessor.from_pretrained.assert_not_called()
                model_class.from_pretrained.assert_not_called()

    def test_nf4_rejects_quantized_vision_and_preserves_unicode_response(self):
        stack, _, model, _ = model_stack('qwen-4b-nf4')
        modules = list(model.named_modules())
        modules[0] = ('model.visual.projection', type('Linear4bit', (), {})())
        model.named_modules = lambda: modules
        worker = qwen_worker.QwenWorker(self.model_dir, 'qwen-4b-nf4')
        with self.assertRaisesRegex(RuntimeError, 'Vision encoder'):
            worker._load(self.torch, stack)
        self.assertIsNone(worker.model)
        stack, _, _, _ = model_stack('qwen-4b-nf4')
        buffer = io.BytesIO()
        Image.new('RGB', (80, 16)).save(buffer, format='PNG')
        with patch.dict(sys.modules, {'torch': self.torch, 'transformers': stack}):
            result = worker.recognize({'images': [base64.b64encode(buffer.getvalue()).decode()]})
        self.assertEqual(result['engine'], 'qwen-4b-nf4')
        self.assertEqual(result['rows'], [{'name': 'ST★avid', 'confidence': 0.0}])
        self.assertEqual(result['confidence_kind'], 'unavailable')

    def test_nvfp4_allows_sm120_and_sm121_but_not_future_unknown_architectures(self):
        for capability in ((12, 0), (12, 1)):
            qwen_worker.preflight(torch_stub(capability=capability), QWEN_PROFILES['qwen-nvfp4'])
        with self.assertRaisesRegex(RuntimeError, 'SM120/121'):
            qwen_worker.preflight(torch_stub(capability=(13, 0)), QWEN_PROFILES['qwen-nvfp4'])

    def test_unknown_profile_and_missing_model_have_actionable_errors(self):
        with self.assertRaisesRegex(ValueError, 'Unknown Qwen'):
            qwen_worker.QwenWorker(self.model_dir, 'qwen-fp4')
        stack, model_class, _, _ = model_stack('qwen')
        with self.assertRaisesRegex(ValueError, 'setup tool'):
            qwen_worker.QwenWorker(self.model_dir / 'missing')._load(self.torch, stack)
        model_class.from_pretrained.assert_not_called()

    def test_wrong_model_architecture_never_loads_weights(self):
        stack, model_class, _, _ = model_stack('qwen36-nvfp4', wrong_architecture=True)
        worker = qwen_worker.QwenWorker(self.model_dir, 'qwen36-nvfp4')
        with self.assertRaisesRegex(ValueError, 'qwen3_5'):
            worker._load(self.torch, stack)
        model_class.from_pretrained.assert_not_called()

    def test_offloaded_parameters_are_rejected_and_adapter_restored(self):
        for device in ('cpu', 'meta', 'disk'):
            with self.subTest(device=device):
                stack, _, _, _ = model_stack('qwen-nvfp4', device=device)
                worker = qwen_worker.QwenWorker(self.model_dir, 'qwen-nvfp4')
                with self.assertRaisesRegex(RuntimeError, 'offload'):
                    worker._load(self.torch, stack)
                self.assertIsNone(worker.model)
                self.assertIsNone(worker.processor)
                self.assertIsNone(worker._adapter)
                self.adapter.restore.assert_called()

    def test_incomplete_quantization_fails_instead_of_silent_bf16_or_partial_conversion(self):
        for profile_id in ('qwen-int8', 'qwen-4b-nf4', 'qwen-nvfp4', 'qwen36-nvfp4'):
            with self.subTest(profile=profile_id):
                stack, _, _, _ = model_stack(profile_id, converted_count=1)
                worker = qwen_worker.QwenWorker(self.model_dir, profile_id)
                with self.assertRaisesRegex(RuntimeError, 'expected .*projections'):
                    worker._load(self.torch, stack)
                self.assertIsNone(worker.model)

    def test_recognize_reuses_model_and_fixed_prompt_without_name_hints(self):
        stack, model_class, model, processor = model_stack('qwen')
        image = Image.new('RGB', (80, 16))
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        request = {'images': [base64.b64encode(buffer.getvalue()).decode()], 'roster': ['NOT A HINT']}
        worker = qwen_worker.QwenWorker(self.model_dir)
        with patch.dict(sys.modules, {'torch': self.torch, 'transformers': stack}):
            first = worker.recognize(request)
            second = worker.recognize(request)
        self.assertEqual(first, second)
        self.assertEqual(first['rows'], [{'name': 'ST★avid', 'confidence': 0.0}])
        self.assertEqual(first['confidence_kind'], 'unavailable')
        model_class.from_pretrained.assert_called_once()
        self.assertEqual(model.generate.call_args.kwargs['max_new_tokens'], 96)
        self.assertFalse(model.generate.call_args.kwargs['do_sample'])
        self.assertTrue(model.generate.call_args.kwargs['use_cache'])
        for call in processor.apply_chat_template.call_args_list:
            self.assertFalse(call.kwargs['enable_thinking'])
            self.assertNotIn('NOT A HINT', str(call))
            self.assertIn('Do not translate or normalize the name.', str(call))

    def test_shared_jsonl_serve_preserves_unicode_on_cp1252_stream(self):
        stack, model_class, _, processor = model_stack('qwen')
        name = 'Ren\u00e9 \u3066\u304c\u3059\u3079\u3063\u3066 \u03a9\u03b1 \u2606\u2665'
        processor.batch_decode.return_value = [name]
        with io.BytesIO() as image:
            Image.new('RGB', (80, 16)).save(image, format='PNG')
            request = json.dumps({'images': [base64.b64encode(image.getvalue()).decode('ascii')]}) + '\n'
        worker = qwen_worker.QwenWorker(self.model_dir)
        with patch.dict(sys.modules, {'torch': self.torch, 'transformers': stack}), \
                io.BytesIO() as buffer, io.TextIOWrapper(buffer, encoding='cp1252') as destination:
            qwen_worker.serve(worker, io.StringIO(request * 2), destination)
            data = buffer.getvalue()
            self.assertTrue(data.isascii())
            responses = [json.loads(line) for line in data.decode('utf-8').splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]['rows'], [{'name': name, 'confidence': 0.0}])
        self.assertEqual(responses[1], responses[0])
        model_class.from_pretrained.assert_called_once()

    def test_invalid_requests_never_load_model(self):
        worker = qwen_worker.QwenWorker(self.model_dir)
        for request in (None, {}, {'images': []}, {'images': ['a']*13}, {'images': [12]},
                        {'images': ['*not base64*']}):
            with self.subTest(request=request), self.assertRaises((ValueError, TypeError)):
                worker.recognize(request)
        self.assertIsNone(worker.model)


class QwenKernelTests(unittest.TestCase):
    def test_exclusions_use_only_meta_model_and_preserve_gates_vision_head_bias_and_alignment(self):
        torch = torch_stub()
        torch.nn = SimpleNamespace(Linear=Linear)
        torch.device = Mock(return_value=contextlib.nullcontext())
        stack, model_class, _, _ = model_stack('qwen36-nvfp4')
        exclusions, converted = qwen_kernels.nvfp4_exclusions(SimpleNamespace(), model_class, torch)
        torch.device.assert_called_once_with('meta')
        self.assertEqual(len(converted), 400)
        self.assertIn('model.visual', exclusions)
        self.assertIn('lm_head', exclusions)
        self.assertIn('biased', exclusions)
        self.assertIn('unaligned', exclusions)
        self.assertTrue(any(name.endswith('in_proj_ba') for name in exclusions))

    def test_selective_kernels_never_replace_blocks_or_allow_reference_fallback(self):
        functions = {key: SimpleNamespace(kernel_layer_name=key,
                                          forward=SimpleNamespace(__module__='transformers'))
                     for key in qwen_kernels.SELECTIVE_KERNEL_SPECS}
        block = type('Qwen3_5GatedDeltaNet', (), {})()
        block._kernel_funcs = functions
        model = SimpleNamespace(named_modules=lambda: [('delta', block)])
        calls = []
        @contextlib.contextmanager
        def mapping(value, inherit_mapping):
            self.assertFalse(inherit_mapping)
            self.assertEqual(set(value), set(qwen_kernels.SELECTIVE_KERNEL_SPECS))
            yield
        def kernelize(function, **kwargs):
            calls.append(kwargs)
            prefix = '_fla_cuda_test.layers' if 'delta_rule' in function.kernel_layer_name else '_mamba_ssm_cuda_test.layers'
            function.forward = SimpleNamespace(__module__=prefix)
        api = SimpleNamespace(Mode=SimpleNamespace(INFERENCE='inference'),
                              LayerRepository=lambda *args, **kwargs: (args, kwargs),
                              use_kernel_mapping=mapping, kernelize=kernelize)
        qwen_kernels.install_selective_kernels(model, api)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(kw == {'mode': 'inference', 'device': 'cuda', 'use_fallback': False} for kw in calls))
        self.assertEqual(block._kernel_funcs, functions)
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            qwen_kernels.install_selective_kernels(SimpleNamespace(named_modules=lambda: []), api)

    def test_native_kernel_resolution_is_mandatory(self):
        functions = {key: SimpleNamespace(kernel_layer_name=key,
                                          forward=SimpleNamespace(__module__='transformers'))
                     for key in qwen_kernels.SELECTIVE_KERNEL_SPECS}
        block = type('Qwen3_5GatedDeltaNet', (), {})()
        block._kernel_funcs = functions
        api = SimpleNamespace(Mode=SimpleNamespace(INFERENCE='inference'),
                              LayerRepository=lambda *args, **kwargs: (args, kwargs),
                              use_kernel_mapping=lambda *args, **kwargs: contextlib.nullcontext(),
                              kernelize=lambda *args, **kwargs: None)
        with self.assertRaisesRegex(RuntimeError, 'pinned native'):
            qwen_kernels.install_selective_kernels(SimpleNamespace(named_modules=lambda: [('delta', block)]), api)


if __name__ == '__main__':
    unittest.main()
