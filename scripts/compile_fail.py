#!/usr/bin/env python3
"""Require each intended diagnostic at its exact source location, plus a positive control."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FE = os.environ.get('FE', 'fe')
CASES = [
    ('asset_mix', '8-0000', 9, 27, 'let b = Amount<B>', 'let b = Amount<A>'),
    ('unit_mix', '8-0000', 3, 22, 'seconds(1)', 'wei(1)'),
    ('invalid_profile', '8-0093', 3, 12, 'Profile<2, 1, 60>', 'Profile<1, 2, 60>'),
    ('lossy_cast', '8-0055', 3, 13, 'fn reject_lossy_cast() {', 'use core::num::IntDowncast\nfn reject_lossy_cast() {'),
    ('missing_mut', '8-0018', 9, 13, 'uses (flag)', 'uses (mut flag)'),
]

with tempfile.TemporaryDirectory(prefix='leash-compile-') as temp:
    project = Path(temp)
    shutil.copy(ROOT / 'fe.toml', project / 'fe.toml')
    shutil.copytree(ROOT / 'src', project / 'src')
    for name, code, line, column, before, after in CASES:
        source = (ROOT / 'compile_fail' / (name + '.fe')).read_text()
        target = project / 'src' / 'negative.fe'
        target.write_text(source)
        result = subprocess.run([FE, 'check', '--color', 'never', str(project)], capture_output=True, text=True)
        diagnostic = result.stdout + result.stderr
        pattern = rf'error\[{re.escape(code)}\]:[\s\S]*?src/negative.fe:{line}:{column}'
        assert result.returncode != 0 and re.search(pattern, diagnostic), (name, diagnostic)
        assert before in source
        positive = source.replace(before, after)
        if name == 'lossy_cast': positive = positive.replace('let _ = wide as u8', 'let _: Option<u8> = wide.downcast()')
        target.write_text(positive)
        result = subprocess.run([FE, 'check', '--color', 'never', str(project)], capture_output=True, text=True)
        assert result.returncode == 0, (name, result.stdout, result.stderr)
        print(f'PASS {name}: diagnostic {code} at {line}:{column}; positive control compiles')
