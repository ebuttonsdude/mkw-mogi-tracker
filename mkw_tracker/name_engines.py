"""Lightweight name-reader profiles. Importing this module never probes hardware."""

QWEN_PROFILES = {
    'qwen': {
        'label': 'Qwen3-VL-8B BF16', 'precision': 'bf16', 'family': 'vl',
        'model_id': 'Qwen/Qwen3-VL-8B-Instruct',
        'revision': '0c351dd01ed87e9c1b53cbc748cba10e6187ff3b',
        'environment': 'vlm-venv', 'model_dir': 'qwen3-vl-8b',
        'peak_vram_gib': 17.48, 'min_free_gib': 22, 'requires_nvfp4': False,
    },
    'qwen-int8': {
        'label': 'Qwen3-VL-8B INT8', 'precision': 'int8', 'family': 'vl',
        'model_id': 'Qwen/Qwen3-VL-8B-Instruct',
        'revision': '0c351dd01ed87e9c1b53cbc748cba10e6187ff3b',
        'environment': 'vlm-int8-venv', 'model_dir': 'qwen3-vl-8b',
        'peak_vram_gib': 11.73, 'min_free_gib': 14, 'requires_nvfp4': False,
    },
    'qwen-4b-nf4': {
        'label': 'Qwen3-VL-4B NF4', 'precision': 'nf4', 'family': 'vl',
        'model_id': 'Qwen/Qwen3-VL-4B-Instruct',
        'revision': 'ebb281ec70b05090aa6165b016eac8ec08e71b17',
        'environment': 'vlm-nf4-venv', 'model_dir': 'qwen3-vl-4b',
        'peak_vram_gib': 4.83, 'min_free_gib': 6.5, 'requires_nvfp4': False,
    },
    'qwen-nvfp4': {
        'label': 'Qwen3-VL-8B NVFP4', 'precision': 'nvfp4', 'family': 'vl',
        'model_id': 'Qwen/Qwen3-VL-8B-Instruct',
        'revision': '0c351dd01ed87e9c1b53cbc748cba10e6187ff3b',
        'environment': 'vlm-nvfp4-venv', 'model_dir': 'qwen3-vl-8b',
        'peak_vram_gib': 8.74, 'min_free_gib': 11, 'requires_nvfp4': True,
    },
    'qwen36-nvfp4': {
        'label': 'Qwen3.6-27B NVFP4', 'precision': 'nvfp4', 'family': '35',
        'model_id': 'Qwen/Qwen3.6-27B',
        'revision': '6a9e13bd6fc8f0983b9b99948120bc37f49c13e9',
        'environment': 'vlm-nvfp4-venv', 'model_dir': 'qwen3.6-27b',
        'peak_vram_gib': 22.98, 'min_free_gib': 25, 'requires_nvfp4': True,
    },
}

# Kept separate: QWEN_PROFILES are the existing Transformers/CUDA backends.
GGUF_PROFILE = {
    'engine': 'qwen-4b-gguf', 'label': 'Qwen3-VL-4B GGUF Q4_K_M',
    'model_id': 'Qwen/Qwen3-VL-4B-Instruct-GGUF',
    'revision': '1cd86afb9a95c410a6038ab3b40d8b578c892266',
    'model_dir': 'qwen3-vl-4b-gguf',
    'model_file': 'Qwen3VL-4B-Instruct-Q4_K_M.gguf',
    'projector_file': 'mmproj-Qwen3VL-4B-Instruct-F16.gguf',
    'model_sha256': '66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a',
    'projector_sha256': '256f3a43bd4205ffef48d6b92715e1e70b5b0e9aef06522584967513a9985331',
}
GENERATIVE_NAME_ENGINES = frozenset({*QWEN_PROFILES, GGUF_PROFILE['engine'], 'external-vision'})
NAME_ENGINES = frozenset({'auto', 'tesseract', 'paddle', *GENERATIVE_NAME_ENGINES})
