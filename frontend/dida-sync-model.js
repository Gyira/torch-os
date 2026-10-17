/** 滴答同步的纯展示/校验函数；不碰 DOM，便于 Node 测试。 */

export function syncErrorText(code) {
  const map = {
    not_configured: "还没有保存滴答登录凭据。",
    bad_cookie: "滴答登录已失效，请重新登录后复制新的 t 值。",
    invalid_credentials: "凭据格式不正确，请重新粘贴。",
    rate_limited: "滴答限流，请稍后再试。",
    network_error: "连不上滴答，请检查网络后重试。",
    invalid_response: "滴答返回格式异常，接口可能已变化。",
    unsupported: "当前运行环境不支持滴答同步。",
    sync_running: "已经有一次同步在进行。",
    csrf_required: "回写滴答需要 _csrf_token。",
    write_forbidden: "滴答拒绝写入（CSRF 或账号权限不足）。",
    http_404: "滴答接口不存在，可能已变更。",
    http_500: "滴答服务暂时不可用。",
  };
  return map[String(code || "")] || `同步失败（${String(code || "unknown")}）。`;
}

export function syncCountsText(counts = {}) {
  const number = (key) => Number(counts[key] || 0);
  const parts = [
    `新建 ${number("created")}`,
    `更新 ${number("updated")}`,
    `未变 ${number("unchanged")}`,
  ];
  if (number("projects_created")) parts.push(`新清单 ${number("projects_created")}`);
  if (number("projects_deleted")) parts.push(`删除清单 ${number("projects_deleted")}`);
  if (number("created_pushed")) parts.push(`回写新增 ${number("created_pushed")}`);
  if (number("updated_pushed")) parts.push(`回写更新 ${number("updated_pushed")}`);
  if (number("moved_pushed")) parts.push(`回写移动 ${number("moved_pushed")}`);
  if (number("restored")) parts.push(`恢复 ${number("restored")}`);
  if (number("completed")) parts.push(`完成 ${number("completed")}`);
  if (number("uncompleted")) parts.push(`取消完成 ${number("uncompleted")}`);
  if (number("local_edits")) parts.push(`本地改动 ${number("local_edits")}`);
  if (number("conflicts")) parts.push(`冲突 ${number("conflicts")}`);
  if (number("remote_deleted")) parts.push(`远端删除 ${number("remote_deleted")}`);
  if (number("deleted_pushed")) parts.push(`回写删除 ${number("deleted_pushed")}`);
  if (number("local_missing")) parts.push(`本地缺失 ${number("local_missing")}`);
  if (number("errors")) parts.push(`错误 ${number("errors")}`);
  return parts.join(" · ");
}

/** 同步结果里的 warning 代码 → 可操作的中文提示。 */
export function syncWarningText(code) {
  const text = String(code || "");
  const map = {
    push_delete_error: "本地删除未能写回滴答（常见原因是缺少 _csrf_token）；请到 设置 → 滴答同步 查看「回写错误」。",
    push_changes_csrf_required: "本地新增/修改未能写回滴答：需要 _csrf_token，请到 设置 → 滴答同步 保存。",
    local_push_no_project: "有本地任务所在清单尚未映射到滴答，已跳过写回。",
    push_needs_pull: "有本地修改等待一次完整同步后再写回（先补全远端原始数据，避免覆盖正文）。",
    attachments_ignored: "附件暂不映射。",
    completed_filtered: "部分已完成任务超出回看范围，已跳过。",
    completed_truncated: "已完成任务超过 2000 条，超出部分已截断。",
    unknown_project: "有任务所属清单已不在远端，已放入收集箱。",
    closed_project_inbox: "有已归档清单的任务已放入收集箱。",
    list_deleted: "有清单在本地已删除，其中的远端任务已跳过。",
    list_create_failed: "有清单创建失败，其中的任务已放入收集箱。",
    duplicate_dida_id: "有重复的滴答任务 ID，重复项已跳过。",
    scan_warning: "有任务笔记无法读取，已跳过。",
    invalid_task: "有任务数据不完整，已跳过。",
    restored_note_unreadable: "滴答恢复的任务在本地回收站里无法读取，已保留原文件，请手动检查。",
  };
  if (map[text]) return map[text];
  if (text.startsWith("project_delete_error_")) return "有远端已删除的清单未能移入回收站，请检查清单目录里是否有非任务文件。";
  if (text.startsWith("task_error_")) return "有任务同步失败，其余任务不受影响。";
  if (text.startsWith("trash_error_")) return "有任务未能移入回收站，请稍后重试。";
  if (text.startsWith("push_error_")) return "有本地修改未能写回滴答，请查看设置页的「回写错误」后重试。";
  if (text.startsWith("push_move_error_")) return "有任务未能移动到滴答清单，请稍后重试。";
  if (text.startsWith("push_apply_error_")) return "滴答已接受修改，但本地同步链接未能保存，请再同步一次。";
  return text;
}

export function conflictReasonText(reason) {
  const map = {
    local_and_remote_changed: "本地和滴答都改过",
    existing_note_differs: "本地已有同 ID 笔记，但内容不同",
    local_note_missing: "本地笔记已不存在",
    remote_deleted: "滴答侧已删除",
  };
  return map[String(reason || "")] || String(reason || "需要人工确认");
}

export function validateSyncForm(form = {}) {
  const interval = Number(form.interval_minutes);
  const completed = Number(form.completed_days);
  const device = Number(form.device_version);
  if (!Number.isInteger(interval) || interval < 5 || interval > 1440) {
    return { ok: false, message: "同步间隔需要是 5–1440 分钟。" };
  }
  if (!Number.isInteger(completed) || completed < 0 || completed > 3650) {
    return { ok: false, message: "已完成回看天数需要是 0–3650。" };
  }
  if (!Number.isInteger(device) || device < 1 || device > 99999) {
    return { ok: false, message: "x-device 版本需要是 1–99999。" };
  }
  if (!String(form.timezone || "").trim()) {
    return { ok: false, message: "时区不能为空。" };
  }
  if (form.enabled && !form.cookie_set && !String(form.cookie || "").trim()) {
    return { ok: false, message: "启用定时同步前先粘贴 cookie。" };
  }
  return { ok: true, message: "" };
}

export function syncBadge(status = {}) {
  if (!status.supported) return { text: "不可用", tone: "candidate" };
  if (status.read_only) return { text: "只读库", tone: "candidate" };
  if (status.running) return { text: "同步中", tone: "active" };
  if (!status.configured) return { text: "未配置", tone: "candidate" };
  if (status.last_error_code) return { text: "需处理", tone: "candidate" };
  return { text: status.enabled ? "已启用" : "已配置", tone: "active" };
}
