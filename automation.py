"""Per-account rules and scheduling. Public profile evidence is checked before sending."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import csv
import difflib
import json
import re
import threading
import time
import unicodedata

TZ = dt.timezone(dt.timedelta(hours=8))
# Conservative defaults protect the accounts: X restricts accounts that reply at high volume.
MIN_INTERVAL, MAX_HOUR, MAX_DAY, AUTHOR_COOLDOWN_DAYS = 600, 4, 20, 7
NO_MATCH_RETRY, TRANSIENT_RETRY, MAX_TRANSIENT_FAILURES = 600, 900, 4
DEFAULT_RULE = dict(tags="", match_mode="all", exclude="", query="", interval=1800,
                    start_time="09:00", end_time="23:00", hour_limit=2, day_limit=10,
                    tone="用中文自然回应原帖的具体观点，15到65字，不推销、不编造经历。", publish_mode="auto")
PUBLISH_MODES = {"auto", "review", "dry"}  # auto: 自动发布; review: 生成草稿人工确认; dry: 只筛选+生成，不发布
SIMILARITY_LIMIT = 0.8  # 与近期已发评论相似度超过此值视为模板化，跳过


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def contains(text, term):
    text, term = normalize(text), normalize(term).lstrip("#")
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9_ .+/-]+", term):
        return bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text))
    return term in text


def groups(value):
    return [[term.strip().lstrip("#") for term in line.split("|") if term.strip().lstrip("#")]
            for line in re.split(r"[\n,，]+", str(value)) if line.strip()]


def match_profile(profile, rule):
    if profile.get("badge") != "blue":
        return False, "蓝 V 无法确认" if profile.get("badge") in {None, "", "unknown"} else "不是蓝 V"
    bio = str(profile.get("bio", ""))
    category = str(profile.get("category", ""))
    evidence = bio + "\n" + category
    if not evidence.strip():
        return False, "主页没有可核对的简介或公开分类"
    tag_groups = groups(rule["tags"])
    if not tag_groups or any(not group for group in tag_groups):
        return False, "未配置博主标签"
    excluded = [term for group in groups(rule["exclude"]) for term in group]
    if any(contains(evidence, term) for term in excluded):
        return False, "博主简介命中排除词"
    hits = [[term for term in group if contains(evidence, term)] for group in tag_groups]
    matched = all(hits) if rule["match_mode"] == "all" else any(hits)
    if not matched:
        return False, "博主简介/分类未满足标签规则"
    return True, "蓝 V；主页命中：" + "、".join("/".join(hit) for hit in hits if hit)


def migrate_rule(values):
    """旧版规则（0 = 不限、30 秒间隔）自动收紧到账号保护范围内，而不是拒绝启动。"""
    rule = dict(DEFAULT_RULE)
    rule.update({k: v for k, v in dict(values or {}).items() if k in rule})
    try:
        rule["interval"] = min(86400, max(MIN_INTERVAL, int(rule["interval"])))
        rule["hour_limit"] = int(rule["hour_limit"]) or DEFAULT_RULE["hour_limit"]
        rule["day_limit"] = int(rule["day_limit"]) or DEFAULT_RULE["day_limit"]
    except (TypeError, ValueError):
        return dict(DEFAULT_RULE, tags=rule.get("tags", ""))
    rule["hour_limit"] = min(MAX_HOUR, max(1, rule["hour_limit"]))
    rule["day_limit"] = min(MAX_DAY, max(1, rule["day_limit"]))
    if rule.get("publish_mode") not in PUBLISH_MODES:
        rule["publish_mode"] = "auto"
    return rule


def too_similar(draft, previous):
    a = normalize(draft)
    return any(difflib.SequenceMatcher(None, a, normalize(p)).ratio() >= SIMILARITY_LIMIT for p in previous if p)


def like_escape(value):
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def validate_rule(values):
    rule = dict(DEFAULT_RULE)
    rule.update({k: v for k, v in values.items() if k in rule})
    for key in ("tags", "exclude", "query", "tone"):
        rule[key] = str(rule[key]).strip()[:2000]
    rule["interval"] = int(rule["interval"])
    if not MIN_INTERVAL <= rule["interval"] <= 86400:
        raise ValueError(f"为保护账号，评论间隔应在 {MIN_INTERVAL} 秒到 86400 秒之间。")
    rule["hour_limit"], rule["day_limit"] = int(rule["hour_limit"]), int(rule["day_limit"])
    if not 1 <= rule["hour_limit"] <= MAX_HOUR:
        raise ValueError(f"为保护账号，每小时上限应为 1 到 {MAX_HOUR} 条。")
    if not 1 <= rule["day_limit"] <= MAX_DAY:
        raise ValueError(f"为保护账号，每天上限应为 1 到 {MAX_DAY} 条。")
    if rule["publish_mode"] not in PUBLISH_MODES:
        raise ValueError("无效发布方式")
    if rule["match_mode"] not in {"all", "any"}:
        raise ValueError("无效标签匹配方式")
    for key in ("start_time", "end_time"):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(rule[key])):
            raise ValueError("运行时段请使用 HH:MM 格式。")
    return rule


def window_delay(rule, now=None):
    now = now or dt.datetime.now(TZ)
    start, end, current = rule["start_time"], rule["end_time"], now.strftime("%H:%M")
    if start == end or (start < end and start <= current < end) or (start > end and (current >= start or current < end)):
        return 0
    hour, minute = map(int, start.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return max(1, (target - now).total_seconds())


class AutomationManager:
    def __init__(self, **dependencies):
        self.__dict__.update(dependencies)
        self.running = set()
        self.failures = {}
        self.guard = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="x-account")
        with self.db() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS automation_accounts(
                id TEXT PRIMARY KEY,label TEXT DEFAULT '',handle TEXT DEFAULT '',
                rule TEXT NOT NULL,enabled INTEGER DEFAULT 0,next_run REAL DEFAULT 0,
                last_run REAL DEFAULT 0,last_error TEXT DEFAULT '',status TEXT DEFAULT '未启动',revision INTEGER DEFAULT 0)""")
            con.execute("""CREATE TABLE IF NOT EXISTS automation_events(
                id INTEGER PRIMARY KEY,created REAL,client_id TEXT,handle TEXT,url TEXT,
                author TEXT,accepted INTEGER,reason TEXT,bio TEXT,badge TEXT)""")
            con.execute("UPDATE automation_accounts SET enabled=0,status='重启后暂停，请检查账号并重新启动'")
            con.execute("UPDATE posts SET status='failed',error='上次草稿生成被中断。' WHERE status='generating'")

    def register(self, client_id, label):
        with self.db() as con:
            con.execute("INSERT OR IGNORE INTO automation_accounts(id,label,rule) VALUES(?,?,?)",
                        (client_id, str(label)[:60], json.dumps(DEFAULT_RULE, ensure_ascii=False)))
            if label:
                con.execute("UPDATE automation_accounts SET label=? WHERE id=?", (str(label)[:60], client_id))

    def record(self, client_id):
        with self.db() as con:
            row = con.execute("SELECT * FROM automation_accounts WHERE id=?", (client_id,)).fetchone()
        if not row:
            raise ValueError("未找到该账号，请先在独立浏览器用户配置中配对扩展。")
        result = dict(row)
        result["rule"] = migrate_rule(json.loads(result["rule"]))
        return result

    def update(self, client_id, **fields):
        allowed = {"handle", "enabled", "next_run", "last_run", "last_error", "status", "revision", "rule", "label"}
        if not fields or set(fields) - allowed:
            raise ValueError("无效账号字段")
        with self.db() as con:
            con.execute("UPDATE automation_accounts SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                        [*fields.values(), client_id])

    def snapshot(self):
        with self.db() as con:
            accounts = [dict(r) for r in con.execute("SELECT * FROM automation_accounts ORDER BY rowid")]
            events = [dict(r) for r in con.execute("SELECT * FROM automation_events ORDER BY id DESC LIMIT 80")]
        sessions = {s["id"]: s for s in self.pool.listing()}
        for account in accounts:
            account["rule"] = migrate_rule(json.loads(account["rule"]))
            client = sessions.get(account["id"], {})
            account["connected"] = client.get("connected", False)
            account["detected_account"] = client.get("account", "")
            account["running"] = account["id"] in self.running
        return dict(accounts=accounts, events=events, timezone="Asia/Shanghai")

    def call(self, client_id, kind, args=None):
        result = self.pool.request(kind, args, client_id=client_id)
        if not result.get("ok"):
            raise ValueError(str(result.get("error", "浏览器操作失败"))[:500])
        return result

    def check(self, client_id, expected=None):
        result = self.call(client_id, "info")
        handle = result.get("account", "")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
            raise ValueError("该浏览器未登录 X。")
        if expected and handle.casefold() != expected.casefold():
            raise ValueError(f"账号已变化，任务原定 @{expected}，当前是 @{handle}，已停止。")
        self.update(client_id, handle=handle)
        return handle

    def action(self, action, args):
        client_id = str(args.get("id", ""))
        account = self.record(client_id)
        if action == "account_save":
            if account["enabled"] or client_id in self.running:
                raise ValueError("请先停止该账号，待当前操作结束后再修改筛选规则。")
            rule = validate_rule(args.get("rule", {}))
            self.update(client_id, rule=json.dumps(rule, ensure_ascii=False), label=str(args.get("label", account["label"]))[:60],
                        revision=account["revision"] + 1, last_error="", status="规则已保存，待启动")
        elif action == "account_check":
            if account["enabled"] or client_id in self.running:
                raise ValueError("请先停止该账号并等待当前操作结束后检查身份。")
            self.executor.submit(self.check_task, client_id)
        elif action == "account_run":
            if not bool(args.get("enabled")):
                self.update(client_id, enabled=0, status="已停止；已开始的操作会完成或进入待核对")
                self.log("已停止账号 " + (account["handle"] or account["label"] or client_id[:8]))
                return
            if account["enabled"]:
                return
            if client_id in self.running:
                raise ValueError("该账号仍有操作未结束。")
            if not self.pool.connected(client_id):
                raise ValueError("该账号的扩展连接已断开，请先重新配对。")
            rule = validate_rule(migrate_rule(account["rule"]))
            if not groups(rule["tags"]) or any(not group for group in groups(rule["tags"])):
                raise ValueError("先填写并保存这个账号的博主标签，再启动自动评论。")
            with self.lock:
                if not self.ai_key() or not self.state["config"]["base_url"] or not self.state["config"]["model"]:
                    raise ValueError("自动评论需要先配置 AI 接口、模型和密钥。")
            if not account["handle"]:
                raise ValueError("请先点击该账号的「检查身份」，核对账号后再启动。")
            with self.db() as con:
                uncertain = con.execute("SELECT COUNT(*) FROM posts WHERE lower(via_account)=lower(?) AND status='uncertain'", (account["handle"],)).fetchone()[0]
            if uncertain:
                raise ValueError("该账号有发送结果待核对，请先处理发布记录。")
            with self.db() as con:
                con.execute("BEGIN IMMEDIATE")
                duplicate = con.execute("SELECT 1 FROM automation_accounts WHERE enabled=1 AND lower(handle)=lower(?) AND id<>?", (account["handle"], client_id)).fetchone()
                if duplicate:
                    raise ValueError("同一个 X 账号已在另一个连接运行，请先停止那个连接，避免重复调度。")
                con.execute("UPDATE automation_accounts SET enabled=1,next_run=?,last_error='',status='等待执行' WHERE id=?", (time.time(), client_id))
            self.log("已启动 @" + account["handle"] + "：仅蓝 V + 已保存的博主主页标签；" + {"auto": "自动生成并发布。", "review": "生成草稿，等你确认后发布。", "dry": "试跑，只筛选和生成，不发布。"}[rule["publish_mode"]])
        else:
            raise ValueError("未知账号操作")

    def autostart(self, client_id):
        """一键启动用：核对身份（与上次记录的账号一致）后，按已保存的规则启动；不满足条件就只写状态，不硬启动。"""
        try:
            account = self.record(client_id)
            if account["enabled"] or client_id in self.running:
                return
            handle = self.check(client_id, expected=account["handle"] or None)
            if not account["revision"]:
                self.update(client_id, last_error="", status="已核对身份 @" + handle + "；请保存这个账号的规则后再启动")
                return
            self.action("account_run", dict(id=client_id, enabled=True))
        except Exception as exc:
            self.update(client_id, enabled=0, last_error=str(exc)[:400], status="一键启动未完成")

    def check_task(self, client_id):
        try:
            handle = self.check(client_id)
            self.update(client_id, last_error="", status="已检查身份：@" + handle)
        except Exception as exc:
            self.update(client_id, enabled=0, last_error=str(exc)[:400], status="身份检查失败")

    def report(self, handle, mode, url, author, badge="", bio="", accepted="", reason="", post_text="", draft="", outcome=""):
        """每日监控记录：data/reports/YYYY-MM-DD.csv（Excel 可直接打开）。"""
        try:
            folder = self.data / "reports"
            folder.mkdir(exist_ok=True)
            now = dt.datetime.now(TZ)
            path = folder / (now.strftime("%Y-%m-%d") + ".csv")
            new = not path.exists()
            with self.guard, path.open("a", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                if new:
                    writer.writerow(["时间(北京)", "运行账号", "发布方式", "帖子链接", "博主", "认证标记", "博主简介", "筛选结果", "理由", "原帖内容", "评论内容", "处理结果", "准确性(人工填写)"])
                writer.writerow([now.strftime("%H:%M:%S"), "@" + handle, mode, url, "@" + author, badge,
                                 str(bio).replace("\n", " ")[:500], accepted, reason,
                                 str(post_text).replace("\n", " ")[:500], draft, outcome, ""])
        except OSError as exc:
            self.log("监控记录写入失败：" + str(exc)[:200])

    def audit(self, client_id, handle, url, profile, accepted, reason):
        with self.db() as con:
            con.execute("INSERT INTO automation_events(created,client_id,handle,url,author,accepted,reason,bio,badge) VALUES(?,?,?,?,?,?,?,?,?)",
                        (time.time(), client_id, handle, url, profile.get("handle", ""), int(accepted), reason,
                         str(profile.get("bio", ""))[:4000], profile.get("badge", "unknown")))

    def limits_delay(self, handle, rule):
        now = time.time()
        midnight = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.db() as con:
            rows = [r[0] for r in con.execute("SELECT COALESCE(published,created) FROM posts WHERE lower(via_account)=lower(?) AND status IN ('sent','sending','uncertain') AND COALESCE(published,created)>=?", (handle, min(now - 3600, midnight)))]
        hour = sorted(t for t in rows if t > now - 3600)
        today = [t for t in rows if t >= midnight]
        if rule["hour_limit"] and len(hour) >= rule["hour_limit"]:
            return max(1, hour[-rule["hour_limit"]] + 3600 - now)
        if rule["day_limit"] and len(today) >= rule["day_limit"]:
            return midnight + 86400 - now
        return 0

    def still_enabled(self, client_id, revision):
        row = self.record(client_id)
        return row["enabled"] and row["revision"] == revision and not self.stop.is_set()

    def cycle(self, client_id):
        account = self.record(client_id)
        rule, revision = account["rule"], account["revision"]
        reserved = None
        dispatched = False
        sent = False
        try:
            if not self.still_enabled(client_id, revision):
                return
            self.update(client_id, status="正在核对登录账号", last_run=time.time())
            handle = self.check(client_id, account["handle"])
            delay = self.limits_delay(handle, rule)
            if delay:
                self.update(client_id, next_run=time.time() + delay, status="已达该账号设置的条数上限，等待下一时段")
                return
            keywords = [term for group in groups(rule["tags"]) for term in group]
            query = rule["query"] or "(" + " OR ".join('"' + re.sub(r'["\\]', '', term) + '"' for term in keywords[:12]) + ")"
            # Ask X for blue-verified original posts only, so each round's few profile checks are not wasted.
            if "filter:blue_verified" not in query:
                query += " filter:blue_verified"
            if "filter:replies" not in query:
                query += " -filter:replies"
            self.update(client_id, status="正在寻找候选帖子；尚未发布")
            candidates = self.call(client_id, "scan", dict(source="search", query=query)).get("posts", [])[:60]
            own_handles = {normalize(s["account"]) for s in self.pool.listing() if s["account"]}
            seen_authors = {}
            checked = 0
            for candidate in candidates:
                if not self.still_enabled(client_id, revision):
                    return
                if candidate.get("promoted") or not str(candidate.get("text", "")).strip():
                    continue
                try:
                    url, post_id = self.canonical_url(candidate.get("url", ""))
                except ValueError:
                    continue
                author = url.split("/")[3]
                if normalize(author) in own_handles:
                    continue
                with self.db() as con:
                    if con.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone():
                        continue
                # Never let any of your accounts reply to the same blogger again within the cooldown:
                # several accounts swarming one author looks coordinated and gets reported.
                with self.db() as con:
                    if con.execute("SELECT 1 FROM posts WHERE lower(url) LIKE lower(?) ESCAPE '\\' AND status IN ('sent','sending','uncertain') AND COALESCE(published,created)>=? LIMIT 1",
                                   ("https://x.com/" + like_escape(author) + "/status/%", time.time() - AUTHOR_COOLDOWN_DAYS * 86400)).fetchone():
                        continue
                # An explicit non-blue badge can be discarded without opening a profile.
                if candidate.get("badge") in {"gold", "grey", "none"}:
                    continue
                if author not in seen_authors:
                    if checked >= 8:
                        break
                    self.update(client_id, status="正在核对博主主页：@" + author)
                    seen_authors[author] = self.call(client_id, "profile", dict(handle=author))
                    checked += 1
                profile = seen_authors[author]
                accepted, reason = match_profile(profile, rule)
                self.audit(client_id, handle, url, profile, accepted, reason)
                mode = rule.get("publish_mode", "auto")
                if not accepted:
                    self.report(handle, mode, url, author, profile.get("badge", ""), profile.get("bio", ""), "跳过", reason,
                                candidate.get("text", ""), "", "未评论")
                    continue
                report_base = (handle, mode, url, author, "blue", profile.get("bio", ""), "通过", reason, candidate.get("text", ""))
                with self.db() as con:
                    inserted = con.execute("INSERT OR IGNORE INTO posts(id,url,author,text,created,status,via_account,profile_bio,badge,match_reason) VALUES(?,?,?,?,?,'generating',?,?,?,?)",
                                           (post_id, url, candidate.get("author", author), str(candidate["text"])[:12000], time.time(),
                                            handle, str(profile.get("bio", ""))[:4000], "blue", reason)).rowcount
                if not inserted:
                    continue
                reserved = post_id
                self.update(client_id, status="蓝 V 与主页标签均匹配，正在生成相关评论")
                with self.lock:
                    config, key = self.state["config"].copy(), self.ai_key()
                config["tone"] = rule["tone"]
                draft = self.ai_draft(dict(author=candidate.get("author", author), text=candidate["text"],
                                           profile_bio=profile.get("bio", ""), target_tags=rule["tags"]), config, key)
                draft = self.validate_draft(draft)
                with self.db() as con:
                    duplicate = con.execute("SELECT 1 FROM posts WHERE draft=? AND status IN ('sent','sending','uncertain') LIMIT 1", (draft,)).fetchone()
                    recent = [r[0] for r in con.execute("SELECT draft FROM posts WHERE status IN ('sent','sending','uncertain') AND draft<>'' ORDER BY COALESCE(published,created) DESC LIMIT 60")]
                if duplicate or too_similar(draft, recent):
                    self.update_post(post_id, draft=draft, status="skipped", error="与近期已发评论相同或过于相似，已跳过")
                    self.report(*report_base, draft, "评论与近期重复/过于相似，跳过")
                    reserved = None
                    continue
                if mode != "auto":
                    note = "试跑模式：只筛选和生成，未发布" if mode == "dry" else "待人工确认：到“待评论”里检查后再发布"
                    self.update_post(post_id, draft=draft, status="pending" if mode == "review" else "skipped", error=note)
                    reserved = None
                    self.update(client_id, status=note, last_error="")
                    self.report(*report_base, draft, note)
                    self.log("@" + handle + " " + note + "；@" + author + "；" + reason)
                    sent = True
                    break
                if not self.still_enabled(client_id, revision):
                    self.update_post(post_id, draft=draft, status="pending", error="任务已停止，草稿保留，未发布")
                    return
                # Re-read the public profile and identity directly before dispatch.
                profile = self.call(client_id, "profile", dict(handle=author))
                accepted, reason = match_profile(profile, rule)
                if not accepted:
                    self.audit(client_id, handle, url, profile, False, "发送前复查：" + reason)
                    self.update_post(post_id, draft=draft, status="skipped", error="发送前复查未通过：" + reason)
                    self.report(*report_base, draft, "发送前复查未通过：" + reason)
                    reserved = None
                    continue
                self.check(client_id, handle)
                if not self.still_enabled(client_id, revision):
                    self.update_post(post_id, draft=draft, status="pending", error="任务已停止，未发布")
                    return
                self.update_post(post_id, draft=draft, status="sending", error="", match_reason=reason)
                self.update(client_id, status="正在发布已通过规则的评论")
                dispatched = True
                result = self.pool.request("publish", dict(url=url, id=post_id, text=draft, account=handle), client_id=client_id)
                if not result.get("ok") or not result.get("sent"):
                    dispatched = result.get("clicked", True)
                    raise ValueError(str(result.get("error", "未确认发送结果，请在 X 核对。")))
                reply_url = ""
                if result.get("reply_url"):
                    reply_url, _ = self.canonical_url(result["reply_url"])
                self.update_post(post_id, status="sent", published=time.time(), reply_url=reply_url, error="")
                self.report(*report_base, draft, "已发布 " + reply_url)
                reserved = None
                self.update(client_id, status="本轮已评论 1 条，等待下个间隔", last_error="")
                self.log("@" + handle + " 已评论 @" + author + "；" + reason)
                sent = True
                break
            self.failures.pop(client_id, None)
            if not sent and reserved is None and self.still_enabled(client_id, revision):
                # Nothing was posted, so look again sooner; browsing search is not a comment.
                self.update(client_id, status="本轮没有符合蓝 V + 主页标签的未评论帖子，稍后再找",
                            next_run=time.time() + min(int(rule["interval"]), NO_MATCH_RETRY))
        except Exception as exc:
            message = str(exc)[:500]
            if reserved:
                self.update_post(reserved, status="uncertain" if dispatched else "failed", error=message)
                self.report(account["handle"], account["rule"].get("publish_mode", "auto"), reserved, "", outcome=("结果待核对：" if dispatched else "失败：") + message)
            transient = not dispatched and "X_WARNING" not in message and "账号已变化" not in message and "未登录" not in message
            count = self.failures.get(client_id, 0) + 1
            self.failures[client_id] = count
            if transient and count < MAX_TRANSIENT_FAILURES:
                # A timeout or a page that loaded slowly should not stop the account all night.
                self.update(client_id, status=f"本轮出错（第 {count} 次），{TRANSIENT_RETRY // 60} 分钟后自动重试；未发布",
                            last_error=message, next_run=time.time() + TRANSIENT_RETRY)
                self.log("账号本轮出错，稍后重试：" + (account["handle"] or client_id[:8]) + "；" + message)
                return
            self.failures.pop(client_id, None)
            self.update(client_id, enabled=0, status="已暂停，请检查原因后重新启动", last_error=message)
            if "X_WARNING" in message:
                # Accounts share this machine and network; one warning means stop everything.
                with self.db() as con:
                    con.execute("UPDATE automation_accounts SET enabled=0,status='X 出现风控提示，已暂停全部账号；请人工检查后再启动' WHERE enabled=1")
                self.log("检测到 X 风控提示，已暂停全部账号：" + message)
            self.log("账号自动评论已暂停：" + (account["handle"] or client_id[:8]) + "；" + message)
        finally:
            current = self.record(client_id)
            if current["enabled"] and current["next_run"] <= time.time():
                self.update(client_id, next_run=time.time() + int(rule["interval"]))
            with self.guard:
                self.running.discard(client_id)

    def loop(self):
        while not self.stop.wait(0.8):
            with self.db() as con:
                due = [dict(r) for r in con.execute("SELECT * FROM automation_accounts WHERE enabled=1 AND next_run<=? ORDER BY next_run,id", (time.time(),))]
            for account in due:
                client_id = account["id"]
                with self.guard:
                    if client_id in self.running:
                        continue
                    if len(self.running) >= 8:
                        self.update(client_id, status="等待其他账号当前任务结束，按计划顺序排队")
                        continue
                    if not self.pool.connected(client_id):
                        self.update(client_id, status="等待这个账号的扩展连接", next_run=time.time() + 10)
                        continue
                    rule = migrate_rule(json.loads(account["rule"]))
                    delay = window_delay(rule)
                    if delay:
                        self.update(client_id, status="等待设定的每日运行时段（北京时间）", next_run=time.time() + delay)
                        continue
                    self.running.add(client_id)
                    self.executor.submit(self.cycle, client_id)
        self.executor.shutdown(wait=False, cancel_futures=True)

    def start(self):
        threading.Thread(target=self.loop, daemon=True, name="x-scheduler").start()
