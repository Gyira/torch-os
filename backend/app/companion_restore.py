"""Offline encrypted Companion recovery. Does not require Obsidian or network access."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .storage_layout import checked_path


def _key(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210000, dklen=32)


def _json(data):
    return json.loads(data.decode("utf-8"))


def _canonical(revision):
    binding = revision["binding"]
    value = {"protocol": 1, "binding": [binding["protocol"], binding["workspace"], binding["account"], binding["remoteRoot"]],
             **{key: revision[key] for key in ("file", "device", "operation")}, "parents": sorted(revision["parents"]),
             "kind": revision["kind"], "content": revision["content"]}
    if "path" in revision:
        value["path"] = revision["path"]
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _path(value):
    import re
    if not isinstance(value, str) or len(value) > 500 or not re.search(r"\.(md|png|jpe?g|webp|gif|avif|pdf)$", value, re.I):
        raise ValueError("恢复路径类型无效")
    parts = value.split("/")
    if any(not p or p in {".", ".."} or p.startswith(".") or p.endswith((" ", ".")) or re.search(r'[\\:\x00-\x1f<>"|?*]', p) or re.match(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", p, re.I) for p in parts):
        raise ValueError("恢复路径越界或不兼容")
    return Path(*parts)


def restore_companion(cloud_directory, pairing_code, pairing_password, destination):
    source, target = checked_path(cloud_directory), checked_path(destination)
    if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("恢复目标必须是与源分离的全新目录")
    if not pairing_code.startswith("TORCHPAIR1:") or len(pairing_code) > 50000:
        raise ValueError("配对恢复材料无效")
    try:
        pair = base64.b64decode(pairing_code[11:], validate=True)
        if pair[0] != 1:
            raise ValueError()
        config = _json(AESGCM(_key(pairing_password, pair[1:17])).decrypt(pair[17:29], pair[29:], None))
        binding = {"protocol": 1, "workspace": config["workspace"], "account": config["account"], "remoteRoot": config["root"]}
        cipher = AESGCM(_key(config["password"], ("torch-sync-v1:" + binding["workspace"]).encode()))
    except Exception as error:
        raise ValueError("配对密码错误或恢复材料损坏") from error

    def read(key):
        path = checked_path(source / key)
        if not path.is_file() or path.stat().st_size > 2 * 1048576:
            raise ValueError("云端对象缺失或超出预算")
        raw = path.read_bytes()
        if not raw or raw[0] != 1:
            raise ValueError("加密版本不支持")
        return cipher.decrypt(raw[1:13], raw[13:], binding["workspace"].encode())

    if _json(read("binding")) != binding:
        raise ValueError("配对绑定不匹配")
    directory = checked_path(source / "commits")
    paths = list(directory.glob("*.json"))
    if len(paths) > 10000:
        raise ValueError("恢复超过历史预算")
    records, operations, parents = {}, {}, set()
    for path in paths:
        record = _json(read("commits/" + path.name)); revision = record["revision"]
        if record["id"] + ".json" != path.name or hashlib.sha256(_canonical(revision)).hexdigest() != record["id"] or revision["binding"] != binding or revision["protocol"] != 1:
            raise ValueError("提交校验或身份不匹配")
        identity = (revision["device"], revision["operation"])
        if identity in operations and operations[identity] != record["id"]:
            raise ValueError("重复操作存在不同内容")
        operations[identity] = record["id"]
        records[record["id"]] = revision
        parents.update(revision["parents"])
    if parents - records.keys():
        raise ValueError("历史不完整，不生成伪成功恢复")
    ready = set()
    while True:
        found = {rid for rid, r in records.items() if rid not in ready and set(r["parents"]) <= ready}
        if not found:
            break
        for rid in found:
            if any(records[p]["file"] != records[rid]["file"] for p in records[rid]["parents"]):
                raise ValueError("祖先属于不同文件")
        ready.update(found)
    if len(ready) != len(records):
        raise ValueError("提交历史含循环")
    heads = {}
    for rid in records.keys() - parents:
        heads.setdefault(records[rid]["file"], []).append(rid)
    files, deleted, used, total = [], [], set(), 0
    for identity, ids in sorted(heads.items()):
        for rid in sorted(ids):
            revision = records[rid]; relative = _path(revision.get("path"))
            if revision["kind"] == "delete" and revision["content"] is None:
                deleted.append(relative.as_posix()); continue
            if revision["kind"] != "put" or not isinstance(revision["content"], str) or len(revision["content"]) != 64 or any(c not in "0123456789abcdef" for c in revision["content"]):
                raise ValueError("版本类型无效")
            raw = read("objects/" + revision["content"])
            if hashlib.sha256(raw).hexdigest() != revision["content"]:
                raise ValueError("正文哈希不匹配")
            if len(ids) > 1 or relative.as_posix().casefold() in used:
                relative = Path("conflicts", rid) / relative
            used.add(revision["path"].casefold())
            total += len(raw)
            if total > 100 * 1048576:
                raise ValueError("恢复超过 100 MiB 预算")
            files.append((relative, raw))
    target.mkdir(parents=True, exist_ok=False)
    for relative, raw in files:
        path = checked_path(target / relative); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
    result = {"version": 1, "workspace_id": binding["workspace"], "files": len(files), "bytes": total, "deleted": deleted, "derivative_only": True}
    (target / "RESTORED.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
