import json
import shutil
import subprocess
from pathlib import Path
import pytest
from backend.app.companion_restore import restore_companion


def make_cloud(tmp_path):
    if not shutil.which("node"):
        pytest.skip("cross-runtime crypto fixture requires Node 24")
    root = Path(__file__).resolve().parents[1]
    source = r'''
import { exportPair } from './companion/core/pairing.ts';
import { SecureTransport } from './companion/core/secure-transport.ts';
import { commit, hash } from './companion/core/protocol.ts';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { dirname } from 'node:path';
const root = process.argv[1], entries = new Map();
const binding = {protocol:1,workspace:'interop-workspace',account:'test',remoteRoot:'/apps/test'};
const blob = {get:async key=>{if(!entries.has(key))throw new Error('Protocol object missing');return entries.get(key)},
 put:async(key,bytes)=>{entries.set(key,bytes); await mkdir(dirname(root+'/'+key),{recursive:true}); await writeFile(root+'/'+key,bytes)},
 list:async prefix=>[...entries.keys()].filter(key=>key.startsWith(prefix))};
const remote = await SecureTransport.open(blob,binding,'sync-password-1234'); await remote.initializeEmpty();
const body = new TextEncoder().encode('中文原文\n\n```py\nprint(1)\n```');
const digest = await hash(body); await remote.putObject(digest,body);
const record = await commit({protocol:1,binding,file:'file',device:'device',operation:'operation',parents:[],kind:'put',content:digest,path:'知识库/中文.md'});
await remote.putCommit(record);
await writeFile(root+'/pair.txt',await exportPair({version:1,workspace:binding.workspace,account:binding.account,root:binding.remoteRoot,password:'sync-password-1234'},'pair-password-1234'));
'''
    cloud = tmp_path / "cloud"; cloud.mkdir()
    subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e", source, str(cloud)], cwd=root, check=True, capture_output=True)
    return cloud, (cloud / "pair.txt").read_text(encoding="utf-8")


def test_python_restores_real_typescript_encryption_without_obsidian(tmp_path):
    cloud, pair = make_cloud(tmp_path)
    target = tmp_path / "restore"
    result = restore_companion(cloud, pair, "pair-password-1234", target)
    assert result["files"] == 1 and result["derivative_only"]
    assert "中文原文" in (target / "知识库/中文.md").read_text(encoding="utf-8")
    assert not list(target.rglob("pair.txt"))
    with pytest.raises(ValueError, match="全新"):
        restore_companion(cloud, pair, "pair-password-1234", target)


def test_wrong_password_and_corrupt_cipher_do_not_create_restore_target(tmp_path):
    cloud, pair = make_cloud(tmp_path)
    target = tmp_path / "restore"
    with pytest.raises(ValueError, match="密码"):
        restore_companion(cloud, pair, "wrong-password", target)
    assert not target.exists()
    next((cloud / "objects").iterdir()).write_bytes(b"broken")
    with pytest.raises(ValueError):
        restore_companion(cloud, pair, "pair-password-1234", target)
    assert not target.exists()
