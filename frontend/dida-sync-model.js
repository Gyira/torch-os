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
    checkins_read_forbidden: "滴答拒绝读取打卡；请补充 _csrf_token 后重试。",
    http_404: "滴答接口不存在，可能已变更。",
    http_500: "滴答服务暂时不可用。",
    EXCEED_QUOTA: "滴答清单数量已达上限，请清理或升级后再试。",
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
  if (number("projects_created_pushed")) parts.push(`回写新清单 ${number("projects_created_pushed")}`);
  if (number("projects_renamed_pushed")) parts.push(`回写清单改名 ${number("projects_renamed_pushed")}`);
  if (number("projects_deleted_pushed")) parts.push(`回写删除清单 ${number("projects_deleted_pushed")}`);
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
  if (number("habits_created")) parts.push(`新建习惯 ${number("habits_created")}`);
  if (number("habits_updated")) parts.push(`更新习惯 ${number("habits_updated")}`);
  if (number("habits_archived")) parts.push(`归档习惯 ${number("habits_archived")}`);
  if (number("habits_linked")) parts.push(`关联习惯 ${number("habits_linked")}`);
  if (number("habits_restored")) parts.push(`恢复习惯 ${number("habits_restored")}`);
  if (number("habits_created_pushed")) parts.push(`回写新习惯 ${number("habits_created_pushed")}`);
  if (number("habits_updated_pushed")) parts.push(`回写习惯 ${number("habits_updated_pushed")}`);
  if (number("habits_deleted_pushed")) parts.push(`回写删除习惯 ${number("habits_deleted_pushed")}`);
  if (number("checkins_updated")) parts.push(`拉取打卡 ${number("checkins_updated")}`);
  if (number("checkins_unchecked")) parts.push(`取消打卡 ${number("checkins_unchecked")}`);
  if (number("checkins_created_pushed")) parts.push(`回写打卡 ${number("checkins_created_pushed")}`);
  if (number("checkins_updated_pushed")) parts.push(`回写打卡更新 ${number("checkins_updated_pushed")}`);
  if (number("checkins_deleted_pushed")) parts.push(`回写取消打卡 ${number("checkins_deleted_pushed")}`);
  if (number("habit_conflicts")) parts.push(`习惯冲突 ${number("habit_conflicts")}`);
  if (number("checkins_conflicts")) parts.push(`打卡冲突 ${number("checkins_conflicts")}`);
  return parts.join(" · ");
}

/** 同步结果里的 warning 代码 → 可操作的中文提示。 */
export function syncWarningText(code) {
  const text = String(code || "");
  const map = {
    push_delete_error: "本地删除未能写回滴答（常见原因是缺少 _csrf_token）；请到 设置 → 滴答同步 查看「回写错误」。",
    push_changes_csrf_required: "本地新增/修改未能写回滴答：需要 _csrf_token，请到 设置 → 滴答同步 保存。",
    local_push_no_project: "有本地任务所在清单尚未映射到滴答，已跳过写回。",
    local_push_unmapped_list: "有任务所在清单尚未映射到滴答，已跳过写回；请先成功创建该清单。",
    push_needs_pull: "有本地修改等待一次完整同步后再写回（先补全远端原始数据，避免覆盖正文）。",
    project_create_unconfirmed: "滴答已接受新建清单，但没有返回确认信息，已按成功处理。",
    project_rename_unconfirmed: "滴答已接受清单改名，但没有返回确认信息，请下次同步后检查名称。",
    project_create_list_gone: "清单在回写期间被改名或删除，已跳过本次新建。",
    project_create_cleanup_failed: "清单新建失败后的远端清理未完成，可能留下一个空清单。",
    project_link_missing: "有本地清单缺少滴答同步链接，清单改名未能回写。",
    project_seen_pending: "新清单等待一次完整拉取确认。",
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
    habit_pull_error: "习惯拉取失败，任务同步不受影响；请查看「上次错误」后重试。",
    habit_push_error: "有习惯定义未能写回滴答，请查看「习惯回写错误」。",
    habit_delete_error: "有本地删除的习惯未能从滴答删除，请稍后重试。",
    habit_push_csrf_required: "习惯写回需要 _csrf_token，请到设置页保存。",
    habit_list_empty_guard: "滴答返回了空习惯列表，本地已按“远端删除”归档；如属异常，重新同步后会自动恢复。",
    habit_archived_not_listed: "部分滴答归档习惯不在列表里，已跳过删除判定。",
    habit_title_ambiguous: "有多个同名习惯，未自动关联；请在设置页手动确认。",
    dida_id_duplicate: "有重复的滴答习惯 ID，重复项已跳过。",
    dida_id_mismatch: "习惯笔记里的滴答 ID 与同步链接不一致，已以链接表为准。",
    habit_rrule_unsupported: "有滴答重复规则无法映射，已保留本地频率。",
    habit_rrule_extras_dropped: "重复规则里的 COUNT/UNTIL 等额外条件未映射。",
    habit_reminder_extra: "滴答有多个提醒，只同步了第一个。",
    habit_real_flattened: "滴答数值型习惯按二元完成处理，不同步部分进度。",
    habit_target_days_clamped: "滴答坚持天数超过 3650，已按 3650 处理。",
    habit_target_days_invalid: "滴答坚持天数格式不正确，已忽略。",
    habit_target_days_rejected: "远端坚持天数与本地历史冲突，已跳过该字段。",
    habit_invalid_title: "有滴答习惯名称为空，已使用“未命名习惯”。",
    habit_missing_note: "本地习惯笔记缺失，已跳过修改。",
    habit_note_missing: "本地习惯笔记缺失，未删除远端习惯。",
    habit_push_needs_pull: "有本地修改等待一次完整拉取后再回写。",
    habit_create_error: "有滴答习惯未能创建到本地。",
    habit_update_error: "有滴答习惯未能更新到本地。",
    habit_archive_error: "有滴答已删除的习惯未能归档到本地。",
    habit_restore_error: "有滴答恢复的习惯未能取消本地归档。",
    archived_habit_not_pushed: "未关联的归档习惯没有在滴答创建。",
    local_note_changed_during_sync: "同步期间本地笔记又被修改，已跳过本次写入。",
    checkin_import_error: "有打卡未能导入本地。",
    checkin_import_skipped: "有打卡在导入时发现本地已改，已转为冲突。",
    checkin_mass_delete_guard: "一次取消的打卡过多，已暂停删除远端记录以防止误删历史。",
    checkin_mass_uncheck_guard: "滴答一次取消了过多打卡，已暂停清空本地记录以防止误删历史。",
  };
  if (map[text]) return map[text];
  if (text.startsWith("project_push_error_")) return "有清单未能写回滴答（新建/改名/删除），请稍后重试或查看「回写错误」。";
  if (text.startsWith("project_delete_error_")) return "有远端已删除的清单未能移入回收站，请检查清单目录里是否有非任务文件。";
  if (text.startsWith("task_error_")) return "有任务同步失败，其余任务不受影响。";
  if (text.startsWith("trash_error_")) return "有任务未能移入回收站，请稍后重试。";
  if (text.startsWith("push_error_")) return "有本地修改未能写回滴答，请查看设置页的「回写错误」后重试。";
  if (text.startsWith("push_move_error_")) return "有任务未能移动到滴答清单，请稍后重试。";
  if (text.startsWith("push_apply_error_")) return "滴答已接受修改，但本地同步链接未能保存，请再同步一次。";
  if (text.startsWith("habit_fetch_error_")) return "滴答习惯列表拉取失败；任务同步不受影响。";
  if (text.startsWith("checkin_query_error_")) return "滴答打卡查询失败；习惯定义仍会同步。";
  if (text.startsWith("habit_")) return "习惯同步有一次失败，请查看设置页状态后重试。";
  return text;
}

export function conflictReasonText(reason) {
  const map = {
    local_and_remote_changed: "本地和滴答都改过",
    existing_note_differs: "本地已有同 ID 笔记，但内容不同",
    local_note_missing: "本地笔记已不存在",
    remote_deleted: "滴答侧已删除",
    first_link_differs: "首次关联时两边设置不同",
    remote_deleted_local_changed: "滴答已删除，但本地也改过",
    local_and_remote_changed: "本地和滴答都改过",
    checkin_both_changed: "本地和滴答的打卡都改过",
    checkin_remote_deleted: "滴答删除了打卡，但本地也改过",
    checkin_remote_changed_local_skipped: "本地是主动休息，但滴答打卡有变化",
    first_checkin_differs: "首次关联时打卡状态不同",
    checkin_conflict: "有打卡需要人工确认",
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
  return { text: status.enabled || status.habits_enabled ? "已启用" : "已配置", tone: "active" };
}

/** 从“未开启”切到“开启”本地删除回写时，保存前需要一次明确确认。 */
export function willArmPushDeletes(payload = {}, status = {}) {
  return Boolean(payload?.push_deletes) && !Boolean(status?.push_deletes);
}
