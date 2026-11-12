"""Pinned helper kernels and projection exclusions for Qwen3.6 NVFP4.

Only the four DeltaNet helper functions are replaced. Full-block replacements
would bypass quantized linear modules through direct weight access.
"""

BASE_EXCLUSIONS = ('model.visual', 'visual', 'lm_head')
SELECTIVE_KERNEL_SPECS = {
    'chunk_gated_delta_rule': ('fla', 'chunk_gated_delta_rule', '24d8640db48c804a224b5da987b81093ff7136d1'),
    'fused_recurrent_gated_delta_rule': ('fla', 'recurrent_gated_delta_rule', '24d8640db48c804a224b5da987b81093ff7136d1'),
    'causal_conv1d_fn': ('mamba-ssm', 'causal_conv1d_fn', '193e6bab0ec6efd5eb778f8e936cfdd4c6e58eb0'),
    'causal_conv1d_update': ('mamba-ssm', 'causal_conv1d_update', '193e6bab0ec6efd5eb778f8e936cfdd4c6e58eb0'),
}


def excluded_name(name, exclusions):
    return any(name == prefix or name.startswith(prefix + '.') for prefix in exclusions)


def nvfp4_exclusions(config, model_class, torch):
    """Inspect a meta-device model, never allocating a second set of GPU weights."""
    with torch.device('meta'):
        template = model_class(config)
    shapes = [(name, module.in_features, module.out_features, module.bias is not None)
              for name, module in template.named_modules() if isinstance(module, torch.nn.Linear)]
    del template
    exclusions = list(BASE_EXCLUSIONS)
    for name, inputs, outputs, bias in shapes:
        sensitive_gate = ('.linear_attn.' in name and name.rsplit('.', 1)[-1]
                          in {'in_proj_a', 'in_proj_b', 'in_proj_ba'})
        if not excluded_name(name, exclusions) and (sensitive_gate or inputs % 64 or outputs % 64 or bias):
            exclusions.append(name)
    return exclusions, {name for name, *_ in shapes if not excluded_name(name, exclusions)}


def install_selective_kernels(model, kernel_api=None):
    if kernel_api is None:
        import kernels as kernel_api
    specs = SELECTIVE_KERNEL_SPECS
    mapping = {name: {'cuda': {kernel_api.Mode.INFERENCE: kernel_api.LayerRepository(
        'kernels-community/' + repo, layer_name=layer, revision=revision)}}
        for name, (repo, layer, revision) in specs.items()}
    functions = {}
    observed = set()
    for _, module in model.named_modules():
        if type(module).__name__ != 'Qwen3_5GatedDeltaNet':
            continue
        for function in getattr(module, '_kernel_funcs', {}).values():
            key = function.kernel_layer_name
            if key not in specs:
                raise RuntimeError(f'Unexpected DeltaNet kernel function {key}')
            observed.add(key)
            functions[id(function)] = function
    if observed != set(specs):
        raise RuntimeError(f'Incomplete selective DeltaNet kernel mapping: {sorted(observed)}')
    with kernel_api.use_kernel_mapping(mapping, inherit_mapping=False):
        for function in functions.values():
            kernel_api.kernelize(function, mode=kernel_api.Mode.INFERENCE, device='cuda', use_fallback=False)
    for function in functions.values():
        key = function.kernel_layer_name
        expected = '_fla_' if specs[key][0] == 'fla' else '_mamba_ssm_'
        if not function.forward.__module__.startswith(expected):
            raise RuntimeError(f'Kernel {key} did not resolve to its pinned native implementation')
