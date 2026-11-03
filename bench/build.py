#!/usr/bin/env python3
"""Pinned Fe O2 and solc legacy/via-IR builds, with reproducible inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'out/bench'
VARIANTS = ('fe', 'legacy', 'via-ir')
FE = os.environ.get('FE', 'fe')
SOLC = os.environ.get('SOLC', str(ROOT / 'bench/solc.cjs'))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def artifact(variant, suffix='bin'):
    base = ROOT / 'out' if variant == 'fe' else OUT / variant
    return base / ('LeashVault.' + suffix)


def bytecode(variant):
    return artifact(variant).read_text().strip().removeprefix('0x')


def wire_abi(abi):
    """Names of parameters vary; selectors, types, mutability and topics must match."""
    return sorted((
        item['type'], item.get('name', ''),
        tuple(field['type'] for field in item.get('inputs', [])),
        tuple(field['type'] for field in item.get('outputs', [])),
        item.get('stateMutability', ''), item.get('anonymous', False),
        tuple(field.get('indexed', False) for field in item.get('inputs', []))
        if item['type'] == 'event' else (),
    ) for item in abi)


def build(no_fe_build=False):
    assert 'fe 26.5.1 (149c758)' in subprocess.check_output([FE, '--version'], text=True)
    if not no_fe_build:
        subprocess.run([FE, 'build', '-O', '2', '--emit', 'bytecode,runtime-bytecode,abi'], cwd=ROOT, check=True)
    version = subprocess.check_output([SOLC, '--version'], text=True).strip()
    assert '0.8.30+commit.73712a01' in version, version
    source = (ROOT / 'bench/LeashVault.sol').read_text()
    for variant in VARIANTS[1:]:
        request = {
            'language': 'Solidity',
            'sources': {'LeashVault.sol': {'content': source}},
            'settings': {
                'evmVersion': 'osaka', 'viaIR': variant == 'via-ir',
                'optimizer': {'enabled': True, 'runs': 200},
                'outputSelection': {'*': {'*': ['abi', 'storageLayout', 'evm.bytecode.object',
                                               'evm.deployedBytecode.object', 'metadata']}},
            },
        }
        encoded = json.dumps(request, indent=2) + '\n'
        raw = subprocess.check_output([SOLC, '--standard-json'], input=encoded, text=True)
        # Native solc and the buffered solc-js adapter both emit JSON.
        result = json.loads(raw)
        errors = [item for item in result.get('errors', []) if item['severity'] == 'error']
        assert not errors, errors
        contract = result['contracts']['LeashVault.sol']['LeashVault']
        layout = contract['storageLayout']['storage']
        assert [(item['label'], item['slot']) for item in layout] == [
            ('agents', '0'), ('epochs', '1'), ('payees', '2'), ('usedNonces', '3'),
            ('isPaused', '4'), ('guardValue', '5')], layout
        record_type = contract['storageLayout']['types'][layout[0]['type']]['value']
        record = contract['storageLayout']['types'][record_type]
        assert record['numberOfBytes'] == '192'
        assert [(field['slot'], field['offset']) for field in record['members']] == [
            ('0', 0), ('1', 0), ('2', 0), ('3', 0), ('4', 0), ('5', 0), ('5', 1)]
        assert wire_abi(contract['abi']) == wire_abi(json.loads(artifact('fe', 'abi.json').read_text()))
        folder = OUT / variant
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'input.json').write_text(encoded)
        (folder / 'output.json').write_text(raw)
        artifact(variant).write_text(contract['evm']['bytecode']['object'] + '\n')
        artifact(variant, 'runtime.bin').write_text(contract['evm']['deployedBytecode']['object'] + '\n')
        artifact(variant, 'abi.json').write_text(json.dumps(contract['abi'], indent=2) + '\n')
        (folder / 'storage-layout.json').write_text(json.dumps(contract['storageLayout'], indent=2) + '\n')
        (folder / 'build.json').write_text(json.dumps({
            'compiler': version, 'settings': request['settings'],
            'input_sha256': sha(encoded.encode()), 'source_sha256': sha(source.encode()),
        }, indent=2) + '\n')
        print(f'PASS solc {variant}: matched ABI/layout, Osaka, optimizer 200; '
              f'{len(bytecode(variant)) // 2} creation bytes', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-fe-build', action='store_true', help='reuse the O2 artifact just built by check.sh')
    build(parser.parse_args().no_fe_build)
