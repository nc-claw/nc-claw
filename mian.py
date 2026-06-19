import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk, Pango

import json, subprocess, threading, os, re
import urllib.request, urllib.error
from pathlib import Path

# ═══════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════

APP_ID = "com.nclaw.NCClaw"
APP_TITLE = "NC-Claw"
VERSION = "3.1.0"
CONFIG_DIR = Path.home() / ".config" / "nc-claw"
CONFIG_FILE = CONFIG_DIR / "config.json"
PAGES_FILE = CONFIG_DIR / "api_pages.json"
CUSTOM_CMDS_FILE = CONFIG_DIR / "custom_commands.json"

DEFAULT_CONFIG = {
    "api_endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "api_key": "",
    "model": "qwen-plus",
    "system_prompt": (
        "You are NC-Claw, a powerful AI system administration assistant.\n"
        "When the user asks for system tasks, provide exact shell commands "
        "inside ```bash code blocks. Explain each command briefly.\n"
        "Warn about destructive operations. Be concise and precise.\n"
        "Format your responses using Markdown.\n"
        "when you need use “：” in the reply , use ">" to replace it"
    ),
    "max_tokens": 4096,
    "temperature": 0.7,
    "confirm_execution": True,
}

DEFAULT_PAGES = [{
    "name": "Example: Get System Info",
    "url": "https://httpbin.org/get",
    "method": "GET",
    "headers": '{"Accept": "application/json"}',
    "body": "",
    "description": "Test GET request",
}]

DEFAULT_CUSTOM_CMDS = [
    {
        "name": "nginx-status",
        "description": "Check nginx service status",
        "command": "systemctl status nginx",
        "enabled": True,
    },
    {
        "name": "nginx-restart",
        "description": "Restart nginx service",
        "command": "sudo systemctl restart nginx",
        "enabled": True,
    },
    {
        "name": "sys-update",
        "description": "Update system packages",
        "command": "sudo apt update && sudo apt upgrade -y",
        "enabled": True,
    },
]

METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]


# ═══════════════════════════════════════════════════════════
# Config / IO
# ═══════════════════════════════════════════════════════════

def load_json(path, default):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            return json.loads(path.read_text('utf-8'))
        except Exception:
            pass
    return default

def save_json(path, data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), 'utf-8')

def load_config():
    cfg = load_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg

def save_config(cfg):
    save_json(CONFIG_FILE, cfg)

def load_pages():
    return load_json(PAGES_FILE, [p.copy() for p in DEFAULT_PAGES])

def save_pages(pages):
    save_json(PAGES_FILE, pages)

def load_custom_cmds():
    return load_json(CUSTOM_CMDS_FILE, [c.copy() for c in DEFAULT_CUSTOM_CMDS])

def save_custom_cmds(data):
    save_json(CUSTOM_CMDS_FILE, data)

def build_system_prompt():
    """Build system prompt including custom command definitions."""
    cfg = load_config()
    base = cfg["system_prompt"]
    cmds = load_custom_cmds()
    enabled = [c for c in cmds if c.get("enabled", True)]
    if enabled:
        base += "\n\n## Available Custom Commands\n"
        base += "You have access to these predefined commands:\n"
        for c in enabled:
            desc = c.get("description", "")
            base += f"- `{c['name']}`: {desc} → Command: `{c['command']}`\n"
        base += ("\nWhen appropriate, suggest these commands by their shortcut name "
                 "inside ```bash code blocks. The system will automatically "
                 "execute the corresponding command.\n")
    return base

def substitute_custom_commands(cmds):
    """Replace custom command names in extracted commands with actual scripts."""
    custom = load_custom_cmds()
    cmap = {}
    for c in custom:
        if c.get("enabled", True):
            cmap[c["name"].lower()] = c["command"]
    result = []
    for cmd in cmds:
        parts = cmd.split(None, 1)
        if parts and parts[0].lower() in cmap:
            expanded = cmap[parts[0].lower()]
            if len(parts) > 1:
                expanded += " " + parts[1]
            result.append(expanded)
        else:
            result.append(cmd)
    return result

def extract_commands(text):
    cmds = []
    for block in re.findall(
        r'```(?:bash|sh|shell|zsh|console)?\s*\n(.*?)```', text, re.DOTALL
    ):
        for line in block.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                line = re.sub(r'^[\$>]\s*', '', line)
                if line:
                    cmds.append(line)
    return substitute_custom_commands(cmds)

def call_ai_api(config, messages):
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    payload = json.dumps({
        "model": config["model"],
        "messages": messages,
        "max_tokens": config["max_tokens"],
        "temperature": config["temperature"],
    }).encode()
    req = urllib.request.Request(
        config["api_endpoint"], data=payload,
        headers=headers, method='POST',
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]

def http_request(method, url, headers, body):
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers)
    req = urllib.request.Request(
        url, data=body.encode() if body else None,
        headers=hdrs, method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": resp.status,
                    "body": resp.read().decode('utf-8', errors='replace'),
                    "error": None}
    except urllib.error.HTTPError as e:
        return {"status": e.code,
                "body": e.read().decode('utf-8', errors='replace'),
                "error": str(e)}
    except Exception as e:
        return {"status": 0, "body": "", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# Markdown Parser
# ═══════════════════════════════════════════════════════════

def _is_block_start(line):
    s = line.strip()
    if not s:
        return True
    if s.startswith('```'):
        return True
    if re.match(r'^#{1,6}\s+', s):
        return True
    if re.match(r'^[-*_]{3,}\s*$', s):
        return True
    if s.startswith('>'):
        return True
    if re.match(r'^[-*+]\s+', s):
        return True
    if re.match(r'^\d+\.\s+', s):
        return True
    return False

def parse_markdown(text):
    blocks = []
    lines = text.split('\n')
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith('```'):
            lang = stripped[3:].strip()
            cl = []
            i += 1
            while i < n and not lines[i].strip().startswith('```'):
                cl.append(lines[i])
                i += 1
            i += 1
            blocks.append(('code', '\n'.join(cl), lang))
            continue
        m = re.match(r'^(#{1,6})\s+(.+)', line)
        if m:
            blocks.append(('heading', m.group(2).strip(), len(m.group(1))))
            i += 1
            continue
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            blocks.append(('hr',))
            i += 1
            continue
        if stripped.startswith('>'):
            ql = []
            while i < n and lines[i].strip().startswith('>'):
                ql.append(re.sub(r'^>\s?', '', lines[i].strip()))
                i += 1
            blocks.append(('quote', '\n'.join(ql)))
            continue
        if re.match(r'^[-*+]\s+', stripped):
            items = []
            while i < n and re.match(r'^[-*+]\s+', lines[i].strip()):
                items.append(re.sub(r'^[-*+]\s+', '', lines[i].strip()))
                i += 1
            blocks.append(('ul', items))
            continue
        if re.match(r'^\d+\.\s+', stripped):
            items = []
            while i < n and re.match(r'^\d+\.\s+', lines[i].strip()):
                items.append(re.sub(r'^\d+\.\s+', '', lines[i].strip()))
                i += 1
            blocks.append(('ol', items))
            continue
        pl = []
        while i < n and lines[i].strip() and not _is_block_start(lines[i]):
            pl.append(lines[i].strip())
            i += 1
        if pl:
            blocks.append(('paragraph', ' '.join(pl)))
    return blocks

def md_inline(text):
    parts = re.split(r'(`[^`]+`)', text)
    result = []
    for part in parts:
        if part.startswith('`') and part.endswith('`') and len(part) > 2:
            code = part[1:-1].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            result.append(f'<span font_family="monospace">{code}</span>')
        else:
            p = part.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            p = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', p)
            p = re.sub(r'\*(.+?)\*', r'<i>\1</i>', p)
            p = re.sub(r'~~(.+?)~~', r'<s>\1</s>', p)
            result.append(p)
    return ''.join(result)


# ═══════════════════════════════════════════════════════════
# Markdown Renderer
# ═══════════════════════════════════════════════════════════

class MarkdownView(Gtk.Box):

    def __init__(self, text, on_run_report=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_homogeneous(False)
        self.on_run_report = on_run_report
        self._render(text)

    def _render(self, text):
        blocks = parse_markdown(text)
        if not blocks:
            lbl = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
            self.append(lbl)
            return
        for block in blocks:
            w = self._make(block)
            if w:
                self.append(w)

    def _make(self, block):
        btype = block[0]

        if btype == 'heading':
            text, level = block[1], block[2]
            lbl = Gtk.Label(xalign=0, wrap=True, use_markup=True)
            try:
                lbl.set_markup(md_inline(text))
            except Exception:
                lbl.set_text(text)
            lbl.add_css_class("title-2" if level <= 2 else "title-4")
            lbl.set_margin_top(4)
            return lbl

        if btype == 'code':
            code, lang = block[1], block[2]
            is_cmd = lang.lower() in ('bash', 'sh', 'shell', 'zsh', 'console', '')

            frame = Gtk.Frame()
            frame.add_css_class("card")
            frame.set_margin_top(2)
            frame.set_margin_bottom(2)

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            vbox.set_margin_top(8)
            vbox.set_margin_bottom(8)
            vbox.set_margin_start(10)
            vbox.set_margin_end(10)

            if lang:
                badge = Gtk.Label(label=lang, xalign=0)
                badge.add_css_class("caption")
                badge.add_css_class("dim-label")
                vbox.append(badge)

            code_lbl = Gtk.Label(label=code, xalign=0)
            code_lbl.add_css_class("monospace")
            code_lbl.set_selectable(True)
            code_lbl.set_wrap(True)
            code_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            vbox.append(code_lbl)

            if is_cmd and self.on_run_report and code.strip():
                cmds = extract_commands(f'```{lang}\n{code}\n```')
                for cmd in cmds:
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                    row.set_margin_top(4)
                    cl = Gtk.Label(label=cmd, xalign=0, hexpand=True)
                    cl.add_css_class("monospace")
                    cl.add_css_class("dim-label")
                    cl.set_ellipsize(Pango.EllipsizeMode.END)
                    row.append(cl)
                    btn = Gtk.Button(label="Run & Report")
                    btn.add_css_class("suggested-action")
                    btn.add_css_class("flat")
                    btn.connect("clicked", lambda _b, c=cmd: self.on_run_report(c))
                    row.append(btn)
                    vbox.append(row)

            frame.set_child(vbox)
            return frame

        if btype == 'paragraph':
            lbl = Gtk.Label(xalign=0, wrap=True, use_markup=True, selectable=True)
            try:
                lbl.set_markup(md_inline(block[1]))
            except Exception:
                lbl.set_text(block[1])
            return lbl

        if btype == 'quote':
            frame = Gtk.Frame()
            lbl = Gtk.Label(xalign=0, wrap=True, use_markup=True)
            try:
                lbl.set_markup(md_inline(block[1]))
            except Exception:
                lbl.set_text(block[1])
            lbl.add_css_class("dim-label")
            lbl.set_margin_top(6)
            lbl.set_margin_bottom(6)
            lbl.set_margin_start(12)
            lbl.set_margin_end(6)
            frame.set_child(lbl)
            return frame

        if btype == 'ul':
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for item in block[1]:
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                bullet = Gtk.Label(label=" \u2022")
                bullet.add_css_class("dim-label")
                hbox.append(bullet)
                lbl = Gtk.Label(xalign=0, wrap=True, use_markup=True, hexpand=True)
                try:
                    lbl.set_markup(md_inline(item))
                except Exception:
                    lbl.set_text(item)
                hbox.append(lbl)
                vbox.append(hbox)
            return vbox

        if btype == 'ol':
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for idx, item in enumerate(block[1], 1):
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                num = Gtk.Label(label=f" {idx}.")
                num.add_css_class("dim-label")
                hbox.append(num)
                lbl = Gtk.Label(xalign=0, wrap=True, use_markup=True, hexpand=True)
                try:
                    lbl.set_markup(md_inline(item))
                except Exception:
                    lbl.set_text(item)
                hbox.append(lbl)
                vbox.append(hbox)
            return vbox

        if btype == 'hr':
            return Gtk.Separator()

        return None


# ═══════════════════════════════════════════════════════════
# Command Card
# ═══════════════════════════════════════════════════════════

class CommandCard(Gtk.Frame):
    """Command card — inherits Gtk.Frame for proper rounded clipping."""

    def __init__(self, command, index, on_report=None):
        super().__init__()
        self.add_css_class("card")
        self.set_margin_bottom(4)
        self.command = command
        self.process = None
        self.running = False
        self.on_report = on_report

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(10)
        inner.set_margin_bottom(10)
        inner.set_margin_start(10)
        inner.set_margin_end(10)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        num = Gtk.Label(label=f"#{index + 1}")
        num.add_css_class("dim-label")
        header.append(num)

        cmd_lbl = Gtk.Label(label=command, xalign=0, hexpand=True)
        cmd_lbl.add_css_class("monospace")
        cmd_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        cmd_lbl.set_selectable(True)
        header.append(cmd_lbl)

        self.run_btn = Gtk.Button(label="Run")
        self.run_btn.add_css_class("suggested-action")
        self.run_btn.connect("clicked", self._on_run)
        header.append(self.run_btn)

        self.stop_btn = Gtk.Button(label="Stop")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.set_visible(False)
        self.stop_btn.connect("clicked", self._on_stop)
        header.append(self.stop_btn)
        inner.append(header)

        self.status_lbl = Gtk.Label(label="Ready", xalign=0)
        self.status_lbl.add_css_class("dim-label")
        inner.append(self.status_lbl)

        self.out_scroll = Gtk.ScrolledWindow()
        self.out_scroll.set_min_content_height(48)
        self.out_scroll.set_max_content_height(200)
        self.out_scroll.set_visible(False)

        self.out_buf = Gtk.TextBuffer()
        out_view = Gtk.TextView(buffer=self.out_buf)
        out_view.add_css_class("monospace")
        out_view.set_editable(False)
        out_view.set_monospace(True)
        out_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        out_view.set_top_margin(6)
        out_view.set_bottom_margin(6)
        out_view.set_left_margin(8)
        out_view.set_right_margin(8)
        self.out_scroll.set_child(out_view)
        self._out_view = out_view
        inner.append(self.out_scroll)

        self.report_btn = Gtk.Button(label="Report to AI")
        self.report_btn.add_css_class("suggested-action")
        self.report_btn.add_css_class("flat")
        self.report_btn.set_visible(False)
        self.report_btn.connect("clicked", self._on_report)
        inner.append(self.report_btn)

        self.set_child(inner)

    def _on_run(self, _btn):
        if self.running:
            return
        self.running = True
        self.run_btn.set_visible(False)
        self.stop_btn.set_visible(True)
        self.report_btn.set_visible(False)
        self.out_scroll.set_visible(True)
        self.out_buf.set_text("")
        self.status_lbl.set_text("Running...")
        self.status_lbl.remove_css_class("dim-label")
        threading.Thread(target=self._execute, daemon=True).start()

    def _on_stop(self, _btn):
        if self.process and self.running:
            try:
                os.killpg(os.getpgid(self.process.pid), 15)
            except Exception:
                pass
            self.running = False
            self.status_lbl.set_text("Terminated")
            self.status_lbl.add_css_class("error")
            self._toggle()

    def _on_report(self, _btn):
        if self.on_report:
            start, end = self.out_buf.get_bounds()
            output = self.out_buf.get_text(start, end, False)
            self.on_report(self.command, output)
            self.report_btn.set_label("Reported \u2713")
            self.report_btn.set_sensitive(False)

    def _execute(self):
        try:
            self.process = subprocess.Popen(
                self.command, shell=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                preexec_fn=os.setsid,
            )
            for line in self.process.stdout:
                if not self.running:
                    break
                GLib.idle_add(self._append, line)
            self.process.wait()
            if self.running:
                ok = self.process.returncode == 0
                GLib.idle_add(self._finish, f"Exit: {self.process.returncode}", ok)
        except Exception as e:
            GLib.idle_add(self._finish, f"Error: {e}", False)
        finally:
            self.running = False
            GLib.idle_add(self._toggle)

    def _append(self, text):
        self.out_buf.insert(self.out_buf.get_end_iter(), text)
        self._out_view.scroll_mark_onscreen(self.out_buf.get_insert())
        return False

    def _finish(self, text, ok):
        self.status_lbl.set_text(text)
        self.status_lbl.add_css_class("success" if ok else "error")
        self.report_btn.set_label("Report to AI")
        self.report_btn.set_sensitive(True)
        self.report_btn.set_visible(self.on_report is not None)
        return False

    def _toggle(self):
        self.run_btn.set_visible(True)
        self.stop_btn.set_visible(False)
        return False

# ═══════════════════════════════════════════════════════════
# Chat Page
# ═══════════════════════════════════════════════════════════

class ChatPage(Gtk.Box):

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.config = load_config()
        self.messages = [
            {"role": "system", "content": build_system_prompt()}
        ]
        self.spinner = None
        self._act_label = None

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_wide_handle(True)
        paned.set_position(520)

        # ── Left ──
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.set_hexpand(True)

        self.msg_scroll = Gtk.ScrolledWindow()
        self.msg_scroll.set_vexpand(True)

        self.msg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.msg_box.set_margin_top(16)
        self.msg_box.set_margin_bottom(16)
        self.msg_box.set_margin_start(16)
        self.msg_box.set_margin_end(16)

        clamp = Adw.Clamp(maximum_size=700, tightening_threshold=300)
        clamp.set_child(self.msg_box)
        self.msg_scroll.set_child(clamp)
        left.append(self.msg_scroll)

        self._add_info(
            "Welcome to NC-Claw! Commands in AI responses have **Run & Report** "
            "buttons — results are sent back to the AI automatically."
        )

        left.append(Gtk.Separator())

        # Input — wrapped in Frame for rounded corners
        inp = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        inp.set_margin_top(8)
        inp.set_margin_bottom(8)
        inp.set_margin_start(12)
        inp.set_margin_end(12)

        self.input_buf = Gtk.TextBuffer()
        self.input_view = Gtk.TextView(buffer=self.input_buf)
        self.input_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.input_view.set_top_margin(8)
        self.input_view.set_bottom_margin(8)
        self.input_view.set_left_margin(10)
        self.input_view.set_right_margin(10)
        self.input_view.set_hexpand(True)
        self.input_view.set_accepts_tab(False)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key)
        self.input_view.add_controller(key_ctrl)

        inp_scroll = Gtk.ScrolledWindow()
        inp_scroll.set_min_content_height(40)
        inp_scroll.set_max_content_height(100)
        inp_scroll.set_hexpand(True)
        inp_scroll.set_child(self.input_view)

        # Wrap in Frame with card class → rounded corners
        input_frame = Gtk.Frame()
        input_frame.add_css_class("card")
        input_frame.set_child(inp_scroll)
        input_frame.set_hexpand(True)
        inp.append(input_frame)

        self.send_btn = Gtk.Button(label="Send")
        self.send_btn.add_css_class("suggested-action")
        self.send_btn.set_valign(Gtk.Align.END)
        self.send_btn.connect("clicked", self._on_send)
        inp.append(self.send_btn)

        left.append(inp)
        paned.set_start_child(left)

        # ── Right ──
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_margin_top(12)
        right.set_margin_bottom(12)
        right.set_margin_start(12)
        right.set_margin_end(12)

        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Extracted Commands")
        title.add_css_class("heading")
        title.add_css_class("title-4")
        title.set_xalign(0)
        title.set_hexpand(True)
        hdr.append(title)

        self.run_all_btn = Gtk.Button(label="Run All")
        self.run_all_btn.add_css_class("suggested-action")
        self.run_all_btn.add_css_class("flat")
        self.run_all_btn.set_visible(False)
        self.run_all_btn.connect("clicked", self._on_run_all)
        hdr.append(self.run_all_btn)

        self.run_report_btn = Gtk.Button(label="Run All & Report")
        self.run_report_btn.add_css_class("suggested-action")
        self.run_report_btn.set_visible(False)
        self.run_report_btn.connect("clicked", self._on_run_all_report)
        hdr.append(self.run_report_btn)
        right.append(hdr)

        right.append(Gtk.Separator())

        cmd_scroll = Gtk.ScrolledWindow()
        cmd_scroll.set_vexpand(True)
        self.cmd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.empty_lbl = Gtk.Label(label="No commands yet.\nAsk me for system tasks!")
        self.empty_lbl.add_css_class("dim-label")
        self.empty_lbl.set_justify(Gtk.Justification.CENTER)
        self.empty_lbl.set_vexpand(True)
        self.empty_lbl.set_valign(Gtk.Align.CENTER)
        self.cmd_box.append(self.empty_lbl)
        cmd_scroll.set_child(self.cmd_box)
        right.append(cmd_scroll)

        paned.set_end_child(right)
        self.append(paned)

    # ── Helpers ──

    def _add_info(self, text):
        renderer = MarkdownView(text)
        renderer.set_halign(Gtk.Align.START)
        self.msg_box.append(renderer)

    def _add_user(self, text):
        frame = Gtk.Frame()
        frame.add_css_class("card")
        frame.set_halign(Gtk.Align.END)
        lbl = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
        lbl.set_margin_top(8)
        lbl.set_margin_bottom(8)
        lbl.set_margin_start(12)
        lbl.set_margin_end(12)
        lbl.set_max_width_chars(60)
        lbl.add_css_class("heading")
        frame.set_child(lbl)
        self.msg_box.append(frame)

    def _add_ai(self, text):
        frame = Gtk.Frame()
        frame.add_css_class("card")
        frame.set_halign(Gtk.Align.START)
        renderer = MarkdownView(text, on_run_report=self._on_run_and_report)
        renderer.set_margin_top(8)
        renderer.set_margin_bottom(8)
        renderer.set_margin_start(12)
        renderer.set_margin_end(12)
        frame.set_child(renderer)
        self.msg_box.append(frame)

    def _add_activity(self, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.add_css_class("dim-label")
        lbl.add_css_class("caption")
        lbl.set_margin_top(4)
        self.msg_box.append(lbl)
        self._act_label = lbl
        return lbl

    def _scroll_bottom(self):
        adj = self.msg_scroll.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()))

    # ── Input ──

    def _on_key(self, _ctrl, keyval, _code, state):
        if keyval == Gdk.KEY_Return and not (state & Gdk.ModifierType.SHIFT_MASK):
            self._on_send(None)
            return True
        return False

    def _on_send(self, _btn):
        buf = self.input_buf
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if not text:
            return
        buf.set_text("")
        self._add_user(text)
        self.messages.append({"role": "user", "content": text})
        self._scroll_bottom()
        self._start_ai_call()

    def _start_ai_call(self):
        self.send_btn.set_sensitive(False)
        self.input_view.set_sensitive(False)
        self.spinner = Gtk.Spinner()
        self.spinner.set_spinning(True)
        self.spinner.set_margin_top(8)
        self.msg_box.append(self.spinner)
        self._scroll_bottom()
        threading.Thread(
            target=self._call_ai_thread,
            args=(list(self.messages),), daemon=True,
        ).start()

    def _call_ai_thread(self, msgs):
        try:
            self.config = load_config()
            result = call_ai_api(self.config, msgs)
            GLib.idle_add(self._on_ai_result, result, None)
        except Exception as e:
            GLib.idle_add(self._on_ai_result, None, str(e))

    def _on_ai_result(self, result, error):
        if self.spinner:
            self.spinner.stop()
            if self.spinner.get_parent():
                self.msg_box.remove(self.spinner)
            self.spinner = None

        self.send_btn.set_sensitive(True)
        self.input_view.set_sensitive(True)
        self.input_view.grab_focus()

        if error:
            self._add_info(f"**Error:** {error}")
            self._scroll_bottom()
            return

        self.messages.append({"role": "assistant", "content": result})
        self._add_ai(result)

        # Clear old commands, add new ones
        self._clear_cmds_panel()
        cmds = extract_commands(result)
        if cmds:
            self._add_cmds_to_panel(cmds)

        self._scroll_bottom()

    # ── Right panel ──

    def _clear_cmds_panel(self):
        """Remove all command cards from the right panel."""
        while True:
            child = self.cmd_box.observe_children().get_item(0)
            if child is None:
                break
            self.cmd_box.remove(child)
        self.run_all_btn.set_visible(False)
        self.run_report_btn.set_visible(False)

    def _add_cmds_to_panel(self, cmds):
        n = self.cmd_box.observe_children().get_n_items()
        for i, c in enumerate(cmds):
            card = CommandCard(c, n + i, on_report=self._on_report_from_panel)
            self.cmd_box.append(card)
        self.run_all_btn.set_visible(True)
        self.run_report_btn.set_visible(True)

    # ── Run & Report (inline) ──

    def _on_run_and_report(self, command):
        self._add_activity(f"Executing: `{command}` ...")
        self._scroll_bottom()
        threading.Thread(
            target=self._run_and_report_thread,
            args=(command,), daemon=True,
        ).start()

    def _run_and_report_thread(self, command):
        try:
            proc = subprocess.Popen(
                command, shell=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=-1,
                preexec_fn=os.setsid,
            )
            output, _ = proc.communicate(timeout=120)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            output = "[Command timed out after 120s]"
            rc = -1
        except Exception as e:
            output = str(e)
            rc = -1
        GLib.idle_add(self._report_result, command, output, rc)

    def _report_result(self, command, output, returncode):
        if len(output) > 8000:
            output = output[:8000] + "\n... [truncated]"
        tag = "✓" if returncode == 0 else "✗"
        report = (
            f"I executed the command you suggested.\n\n"
            f"**Command:**\n```bash\n{command}\n```\n\n"
            f"**Result** ({tag} exit code {returncode}):\n```\n{output}\n```"
        )
        self._add_activity("Sending results to AI...")
        self.messages.append({"role": "user", "content": report})
        self._start_ai_call()
        return False

    def _on_report_from_panel(self, command, output):
        if len(output) > 8000:
            output = output[:8000] + "\n... [truncated]"
        report = (
            f"I ran this command:\n```bash\n{command}\n```\n\n"
            f"Output:\n```\n{output}\n```"
        )
        self._add_activity(f"Reporting `{command}` to AI...")
        self.messages.append({"role": "user", "content": report})
        self._start_ai_call()

    # ── Run All ──

    def _on_run_all(self, _btn):
        children = self.cmd_box.observe_children()
        for i in range(children.get_n_items()):
            child = children.get_item(i)
            if isinstance(child, CommandCard) and not child.running:
                child.run_btn.emit("clicked")

    def _on_run_all_report(self, _btn):
        cards = []
        children = self.cmd_box.observe_children()
        for i in range(children.get_n_items()):
            child = children.get_item(i)
            if isinstance(child, CommandCard):
                cards.append(child)
        if not cards:
            return
        self.run_report_btn.set_sensitive(False)
        self.run_all_btn.set_sensitive(False)
        self._add_activity(f"Running {len(cards)} commands sequentially...")
        self._scroll_bottom()
        threading.Thread(
            target=self._run_all_report_thread,
            args=(cards,), daemon=True,
        ).start()

    def _run_all_report_thread(self, cards):
        results = []
        for idx, card in enumerate(cards):
            cmd = card.command
            GLib.idle_add(
                self._update_activity,
                f"Running [{idx + 1}/{len(cards)}]: {cmd}",
            )
            GLib.idle_add(card.run_btn.emit, "clicked")
            try:
                proc = subprocess.Popen(
                    cmd, shell=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=-1,
                    preexec_fn=os.setsid,
                )
                output, _ = proc.communicate(timeout=120)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                output = "[Command timed out after 120s]"
                rc = -1
            except Exception as e:
                output = str(e)
                rc = -1
            if len(output) > 4000:
                output = output[:4000] + "\n... [truncated]"
            results.append((cmd, output, rc))
        GLib.idle_add(self._send_all_report, results)

    def _update_activity(self, text):
        if self._act_label:
            self._act_label.set_text(text)
        return False

    def _send_all_report(self, results):
        parts = []
        for idx, (cmd, output, rc) in enumerate(results, 1):
            tag = "✓" if rc == 0 else "✗"
            parts.append(
                f"### Command {idx}\n```bash\n{cmd}\n```\n"
                f"**Result** ({tag} exit {rc}):\n```\n{output}\n```\n"
            )
        report = (
            f"I executed all {len(results)} commands. "
            f"Here are the results:\n\n" + "\n".join(parts)
        )
        self.messages.append({"role": "user", "content": report})
        self._add_activity("All commands done. Sending results to AI...")
        self.run_report_btn.set_sensitive(True)
        self.run_all_btn.set_sensitive(True)
        self._start_ai_call()
        return False


# ═══════════════════════════════════════════════════════════
# Commands Page
# ═══════════════════════════════════════════════════════════

class CommandsPage(Gtk.Box):

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_margin_top(16)
        self.set_margin_bottom(16)
        self.set_margin_start(16)
        self.set_margin_end(16)

        title = Gtk.Label(label="Command Runner")
        title.add_css_class("title-2")
        title.set_xalign(0)
        self.append(title)

        desc = Gtk.Label(label="Enter a command to execute directly")
        desc.add_css_class("dim-label")
        desc.set_xalign(0)
        self.append(desc)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("e.g. ls -la, df -h, uname -a")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", lambda _: self._run())
        row.append(self.entry)
        btn = Gtk.Button(label="Execute")
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda _: self._run())
        row.append(btn)
        self.append(row)

        quick_frame = Gtk.Frame()
        quick_frame.add_css_class("card")
        qb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        qb.set_margin_top(8)
        qb.set_margin_bottom(8)
        qb.set_margin_start(8)
        qb.set_margin_end(8)
        ql = Gtk.Label(label="Quick:")
        ql.add_css_class("dim-label")
        qb.append(ql)
        for cmd, lbl in [
            ("uname -a", "System"), ("df -h", "Disk"),
            ("free -h", "Memory"), ("ps aux --sort=-%mem | head -10", "Processes"),
            ("ip addr", "Network"), ("uptime", "Uptime"),
        ]:
            b = Gtk.Button(label=lbl)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, c=cmd: (self.entry.set_text(c), self._run()))
            qb.append(b)
        quick_frame.set_child(qb)
        self.append(quick_frame)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.out_buf = Gtk.TextBuffer()
        out_view = Gtk.TextView(buffer=self.out_buf)
        out_view.add_css_class("monospace")
        out_view.set_editable(False)
        out_view.set_monospace(True)
        out_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        out_view.set_top_margin(8)
        out_view.set_bottom_margin(8)
        out_view.set_left_margin(8)
        out_view.set_right_margin(8)
        scroll.set_child(out_view)
        self._out_view = out_view
        self.append(scroll)

    def _run(self):
        cmd = self.entry.get_text().strip()
        if not cmd:
            return
        self.out_buf.set_text(f"$ {cmd}\n")
        threading.Thread(target=self._exec, args=(cmd,), daemon=True).start()

    def _exec(self, cmd):
        try:
            p = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            for line in p.stdout:
                GLib.idle_add(self._append, line)
            p.wait()
            GLib.idle_add(self._append, f"\n[Exit: {p.returncode}]\n")
        except Exception as e:
            GLib.idle_add(self._append, f"\nError: {e}\n")

    def _append(self, text):
        self.out_buf.insert(self.out_buf.get_end_iter(), text)
        self._out_view.scroll_mark_onscreen(self.out_buf.get_insert())
        return False


# ═══════════════════════════════════════════════════════════
# API Editor Page — with Custom Commands
# ═══════════════════════════════════════════════════════════
class APIEditorPage(Gtk.Box):

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.pages = load_pages()
        self.custom_cmds = load_custom_cmds()
        self.mode = "api"
        self.selected_api = -1
        self.selected_cmd = -1
        self._rebuilding = False

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(280)

        # ─── Left sidebar ───
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_margin_top(12)
        left.set_margin_bottom(12)
        left.set_margin_start(12)
        left.set_margin_end(4)

        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        mode_box.add_css_class("linked")
        self.api_mode_btn = Gtk.ToggleButton(label="API Pages", active=True)
        self.api_mode_btn.set_hexpand(True)
        self.cmd_mode_btn = Gtk.ToggleButton(label="Commands")
        self.cmd_mode_btn.set_group(self.api_mode_btn)
        self.cmd_mode_btn.set_hexpand(True)
        self.api_mode_btn.connect("toggled", self._on_mode_toggle)
        self.cmd_mode_btn.connect("toggled", self._on_mode_toggle)
        mode_box.append(self.api_mode_btn)
        mode_box.append(self.cmd_mode_btn)
        left.append(mode_box)

        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.list_title = Gtk.Label(label="API Pages", xalign=0)
        self.list_title.add_css_class("heading")
        self.list_title.set_hexpand(True)
        hdr.append(self.list_title)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.add_css_class("suggested-action")
        add_btn.add_css_class("circular")
        add_btn.connect("clicked", self._on_add)
        hdr.append(add_btn)
        left.append(hdr)
        left.append(Gtk.Separator())

        self.list_box = Gtk.ListBox()
        self.list_box.add_css_class("boxed-list")
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.connect("row-selected", self._on_row)
        left.append(self.list_box)

        paned.set_start_child(left)

        # ─── Right: editor area ───
        rscroll = Gtk.ScrolledWindow()
        rscroll.set_vexpand(True)

        editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        editor_box.set_margin_top(16)
        editor_box.set_margin_bottom(16)
        editor_box.set_margin_start(16)
        editor_box.set_margin_end(16)

        # Empty placeholder
        self.empty_label = Gtk.Label(
            label="Select an item from the list\nor click + to create one",
        )
        self.empty_label.add_css_class("dim-label")
        self.empty_label.add_css_class("title-4")
        self.empty_label.set_vexpand(True)
        self.empty_label.set_justify(Gtk.Justification.CENTER)

        # API form
        self.api_form_box = self._build_api_form()
        self.api_form_box.set_visible(False)

        # Command form
        self.cmd_form_box = self._build_cmd_form()
        self.cmd_form_box.set_visible(False)

        editor_box.append(self.empty_label)
        editor_box.append(self.api_form_box)
        editor_box.append(self.cmd_form_box)

        rscroll.set_child(editor_box)
        paned.set_end_child(rscroll)
        self.append(paned)

        self._rebuild_list()

    # ─── Utility ───

    def _lbl(self, text):
        l = Gtk.Label(label=text, xalign=0)
        l.add_css_class("dim-label")
        l.add_css_class("caption")
        return l

    def _add_textview(self, parent, min_h, max_h, buf, editable=True):
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(min_h)
        scroll.set_max_content_height(max_h)
        scroll.add_css_class("card")
        view = Gtk.TextView(buffer=buf)
        view.add_css_class("monospace")
        view.set_editable(editable)
        view.set_monospace(True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_margin_top(8)
        view.set_margin_bottom(8)
        view.set_left_margin(8)
        view.set_right_margin(8)
        scroll.set_child(view)
        parent.append(scroll)

    def _show_form(self, which):
        """Show one form and hide the rest. which: 'empty'|'api'|'cmd'"""
        self.empty_label.set_visible(which == "empty")
        self.api_form_box.set_visible(which == "api")
        self.cmd_form_box.set_visible(which == "cmd")

    # ─── Build API form ───

    def _build_api_form(self):
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        form.append(self._lbl("Name"))
        self.api_name_entry = Gtk.Entry()
        self.api_name_entry.set_placeholder_text("API Name")
        form.append(self.api_name_entry)

        form.append(self._lbl("Request"))
        url_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.api_method_dd = Gtk.DropDown.new(Gtk.StringList.new(METHODS))
        self.api_method_dd.set_size_request(110, -1)
        url_row.append(self.api_method_dd)
        self.api_url_entry = Gtk.Entry()
        self.api_url_entry.set_placeholder_text("https://api.example.com/endpoint")
        self.api_url_entry.set_hexpand(True)
        url_row.append(self.api_url_entry)
        form.append(url_row)

        form.append(self._lbl("Description"))
        self.api_desc_entry = Gtk.Entry()
        self.api_desc_entry.set_placeholder_text("Brief description")
        form.append(self.api_desc_entry)

        form.append(self._lbl("Headers (JSON)"))
        self.api_headers_buf = Gtk.TextBuffer()
        self._add_textview(form, 80, 140, self.api_headers_buf)

        form.append(self._lbl("Request Body"))
        self.api_body_buf = Gtk.TextBuffer()
        self._add_textview(form, 80, 140, self.api_body_buf)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        s = Gtk.Button(label="Send Request")
        s.add_css_class("suggested-action")
        s.connect("clicked", self._on_api_send)
        btns.append(s)
        sv = Gtk.Button(label="Save")
        sv.add_css_class("flat")
        sv.connect("clicked", self._on_api_save)
        btns.append(sv)
        d = Gtk.Button(label="Delete")
        d.add_css_class("destructive-action")
        d.connect("clicked", self._on_api_delete)
        btns.append(d)
        form.append(btns)

        form.append(self._lbl("Response"))
        self.api_resp_buf = Gtk.TextBuffer()
        self._add_textview(form, 120, 300, self.api_resp_buf, editable=False)

        return form

    # ─── Build Command form ───

    def _build_cmd_form(self):
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        form.append(self._lbl("Command Name (shortcut)"))
        self.cmd_name_entry = Gtk.Entry()
        self.cmd_name_entry.set_placeholder_text("e.g. deploy, backup, restart-nginx")
        form.append(self.cmd_name_entry)

        form.append(self._lbl("Description"))
        self.cmd_desc_entry = Gtk.Entry()
        self.cmd_desc_entry.set_placeholder_text("What this command does")
        form.append(self.cmd_desc_entry)

        form.append(self._lbl("System Command"))
        self.cmd_script_entry = Gtk.Entry()
        self.cmd_script_entry.set_placeholder_text("sudo systemctl restart nginx")
        form.append(self.cmd_script_entry)
        # Enabled switch row
        switch_frame = Gtk.Frame()
        switch_frame.add_css_class("card")
        switch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        switch_row.set_margin_top(10)
        switch_row.set_margin_bottom(10)
        switch_row.set_margin_start(14)
        switch_row.set_margin_end(14)
        switch_lbl = Gtk.Label(label="Enabled — AI can suggest this command", xalign=0)
        switch_lbl.set_hexpand(True)
        switch_row.append(switch_lbl)
        self.cmd_enabled_switch = Gtk.Switch()
        self.cmd_enabled_switch.set_active(True)
        self.cmd_enabled_switch.set_valign(Gtk.Align.CENTER)
        switch_row.append(self.cmd_enabled_switch)
        switch_frame.set_child(switch_row)
        form.append(switch_frame)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sv = Gtk.Button(label="Save Command")
        sv.add_css_class("suggested-action")
        sv.connect("clicked", self._on_cmd_save)
        btns.append(sv)
        d = Gtk.Button(label="Delete")
        d.add_css_class("destructive-action")
        d.connect("clicked", self._on_cmd_delete)
        btns.append(d)
        form.append(btns)

        return form

    # ─── Mode toggle ───

    def _on_mode_toggle(self, btn):
        if not btn.get_active():
            return
        if self.api_mode_btn.get_active():
            self.mode = "api"
            self.list_title.set_text("API Pages")
        else:
            self.mode = "cmd"
            self.list_title.set_text("Custom Commands")
        self._show_form("empty")
        self._rebuild_list()

    # ─── List rebuild ───

    def _rebuild_list(self):
        self._rebuilding = True

        while True:
            row = self.list_box.get_row_at_index(0)
            if row is None:
                break
            self.list_box.remove(row)

        items = self.pages if self.mode == "api" else self.custom_cmds

        for i, item in enumerate(items):
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(12)
            box.set_margin_end(12)

            if self.mode == "api":
                method = item.get("method", "GET")
                badge = Gtk.Label(label=method)
                badge.add_css_class("caption")
                badge.add_css_class("monospace")
                box.append(badge)
            else:
                enabled = item.get("enabled", True)
                icon_name = "emblem-default-symbolic" if enabled else "dialog-error-symbolic"
                icon = Gtk.Image.new_from_icon_name(icon_name)
                icon.set_pixel_size(16)
                box.append(icon)

            n = Gtk.Label(label=item.get("name", ""), xalign=0, hexpand=True)
            n.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(n)
            row.set_child(box)
            self.list_box.append(row)

        # Restore selection
        sel = self.selected_api if self.mode == "api" else self.selected_cmd
        items = self.pages if self.mode == "api" else self.custom_cmds
        if 0 <= sel < len(items):
            r = self.list_box.get_row_at_index(sel)
            if r:
                self.list_box.select_row(r)
            # Show the form for the selected item
            if self.mode == "api":
                self._populate_api(sel)
                self._show_form("api")
            else:
                self._populate_cmd(sel)
                self._show_form("cmd")

        self._rebuilding = False

    # ─── Row selection ───

    def _on_row(self, _lb, row):
        if self._rebuilding or row is None:
            return
        idx = row.get_index()
        items = self.pages if self.mode == "api" else self.custom_cmds
        if 0 <= idx < len(items):
            if self.mode == "api":
                self.selected_api = idx
                self._populate_api(idx)
                self._show_form("api")
            else:
                self.selected_cmd = idx
                self._populate_cmd(idx)
                self._show_form("cmd")

    # ─── API populate / save / send ───

    def _populate_api(self, idx):
        page = self.pages[idx]
        self.api_name_entry.set_text(page.get("name", ""))
        self.api_url_entry.set_text(page.get("url", ""))
        self.api_desc_entry.set_text(page.get("description", ""))
        try:
            self.api_method_dd.set_selected(METHODS.index(page.get("method", "GET")))
        except ValueError:
            self.api_method_dd.set_selected(0)
        self.api_headers_buf.set_text(page.get("headers", "{}"))
        self.api_body_buf.set_text(page.get("body", ""))
        self.api_resp_buf.set_text("")

    def _read_api_form(self):
        hs, he = self.api_headers_buf.get_bounds()
        bs, be = self.api_body_buf.get_bounds()
        return {
            "name": self.api_name_entry.get_text(),
            "url": self.api_url_entry.get_text(),
            "method": METHODS[self.api_method_dd.get_selected()],
            "headers": self.api_headers_buf.get_text(hs, he, False),
            "body": self.api_body_buf.get_text(bs, be, False),
            "description": self.api_desc_entry.get_text(),
        }

    def _on_api_save(self, _):
        if self.selected_api < 0 or self.selected_api >= len(self.pages):
            return
        self.pages[self.selected_api] = self._read_api_form()
        save_pages(self.pages)
        self._rebuild_list()

    def _on_api_delete(self, _):
        if self.selected_api < 0:
            return
        self.pages.pop(self.selected_api)
        save_pages(self.pages)
        self.selected_api = -1
        self._show_form("empty")
        self._rebuild_list()

    def _on_api_send(self, _):
        data = self._read_api_form()
        if not data["url"] or data["url"] == "https://":
            self.api_resp_buf.set_text("Enter a valid URL")
            return
        try:
            hdrs = json.loads(data["headers"]) if data["headers"].strip() else {}
        except json.JSONDecodeError:
            self.api_resp_buf.set_text("Invalid JSON in headers")
            return
        self.api_resp_buf.set_text("Sending...")
        threading.Thread(
            target=self._do_api_req,
            args=(data["method"], data["url"], hdrs, data["body"]),
            daemon=True,
        ).start()

    def _do_api_req(self, method, url, headers, body):
        result = http_request(method, url, headers, body)
        GLib.idle_add(self._show_api_resp, result)

    def _show_api_resp(self, result):
        text = f"Status: {result['status']}\n{'─' * 50}\n"
        if result.get("error"):
            text += f"Error: {result['error']}\n\n"
        text += result.get("body", "")
        self.api_resp_buf.set_text(text)
        return False

    # ─── Command populate / save / delete ───

    def _populate_cmd(self, idx):
        cmd = self.custom_cmds[idx]
        self.cmd_name_entry.set_text(cmd.get("name", ""))
        self.cmd_desc_entry.set_text(cmd.get("description", ""))
        self.cmd_script_entry.set_text(cmd.get("command", ""))
        self.cmd_enabled_switch.set_active(cmd.get("enabled", True))

    def _read_cmd_form(self):
        return {
            "name": self.cmd_name_entry.get_text().strip(),
            "description": self.cmd_desc_entry.get_text().strip(),
            "command": self.cmd_script_entry.get_text().strip(),
            "enabled": self.cmd_enabled_switch.get_active(),
        }

    def _on_cmd_save(self, _):
        data = self._read_cmd_form()
        if not data["name"]:
            return
        if 0 <= self.selected_cmd < len(self.custom_cmds):
            self.custom_cmds[self.selected_cmd] = data
        else:
            self.custom_cmds.append(data)
            self.selected_cmd = len(self.custom_cmds) - 1
        save_custom_cmds(self.custom_cmds)
        self._rebuild_list()

    def _on_cmd_delete(self, _):
        if self.selected_cmd < 0:
            return
        self.custom_cmds.pop(self.selected_cmd)
        save_custom_cmds(self.custom_cmds)
        self.selected_cmd = -1
        self._show_form("empty")
        self._rebuild_list()

    # ─── Add ───

    def _on_add(self, _):
        if self.mode == "api":
            self.pages.append({
                "name": "New API Page", "url": "https://",
                "method": "GET",
                "headers": '{"Content-Type": "application/json"}',
                "body": "", "description": "",
            })
            save_pages(self.pages)
            self.selected_api = len(self.pages) - 1
            self._rebuild_list()
        else:
            self.custom_cmds.append({
                "name": "new-command",
                "description": "Describe what this command does",
                "command": "systemctl status <service>",
                "enabled": True,
            })
            save_custom_cmds(self.custom_cmds)
            self.selected_cmd = len(self.custom_cmds) - 1
            self._rebuild_list()
# ═══════════════════════════════════════════════════════════
# Settings Page
# ═══════════════════════════════════════════════════════════

class SettingsPage(Gtk.Box):

    def __init__(self, toast_overlay):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay = toast_overlay
        self.config = load_config()

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        title = Gtk.Label(label="Settings")
        title.add_css_class("title-1")
        title.set_xalign(0)
        content.append(title)

        api_grp = Adw.PreferencesGroup()
        api_grp.set_title("AI API Configuration")
        api_grp.set_description("Configure your AI provider")

        self.ep_row = Adw.EntryRow()
        self.ep_row.set_title("API Endpoint")
        self.ep_row.set_text(self.config["api_endpoint"])
        api_grp.add(self.ep_row)

        if hasattr(Adw, "PasswordEntryRow"):
            self.key_row = Adw.PasswordEntryRow()
        else:
            self.key_row = Adw.EntryRow()
        self.key_row.set_title("API Key")
        self.key_row.set_text(self.config["api_key"])
        api_grp.add(self.key_row)

        self.model_row = Adw.EntryRow()
        self.model_row.set_title("Model")
        self.model_row.set_text(self.config["model"])
        api_grp.add(self.model_row)

        content.append(api_grp)

        beh = Adw.PreferencesGroup()
        beh.set_title("Behavior")

        self.tokens_row = Adw.EntryRow()
        self.tokens_row.set_title("Max Tokens")
        self.tokens_row.set_text(str(self.config["max_tokens"]))
        beh.add(self.tokens_row)

        temp_row = Adw.ActionRow()
        temp_row.set_title("Temperature")
        temp_row.set_subtitle("0.0 = deterministic, 2.0 = creative")
        self.temp_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.0, 2.0, 0.1,
        )
        self.temp_scale.set_value(self.config["temperature"])
        self.temp_scale.set_size_request(200, -1)
        self.temp_scale.set_hexpand(True)
        self.temp_scale.set_digits(1)
        self.temp_scale.set_value_pos(Gtk.PositionType.RIGHT)
        temp_row.add_suffix(self.temp_scale)
        beh.add(temp_row)

        self.confirm_row = Adw.SwitchRow()
        self.confirm_row.set_title("Confirm Before Execution")
        self.confirm_row.set_subtitle("Ask confirmation before running commands")
        self.confirm_row.set_active(self.config.get("confirm_execution", True))
        beh.add(self.confirm_row)

        content.append(beh)

        pl = Gtk.Label(label="System Prompt")
        pl.add_css_class("heading")
        pl.set_xalign(0)
        content.append(pl)

        frame = Gtk.Frame()
        frame.add_css_class("card")
        pscroll = Gtk.ScrolledWindow()
        pscroll.set_min_content_height(120)
        pscroll.set_max_content_height(250)
        self.prompt_buf = Gtk.TextBuffer()
        self.prompt_buf.set_text(self.config["system_prompt"])
        pview = Gtk.TextView(buffer=self.prompt_buf)
        pview.add_css_class("monospace")
        pview.set_monospace(True)
        pview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        pview.set_margin_top(8)
        pview.set_margin_bottom(8)
        pview.set_left_margin(8)
        pview.set_right_margin(8)
        pscroll.set_child(pview)
        frame.set_child(pscroll)
        content.append(frame)

        save_btn = Gtk.Button(label="Save Configuration")
        save_btn.add_css_class("suggested-action")
        save_btn.set_margin_top(8)
        save_btn.connect("clicked", self._on_save)
        content.append(save_btn)

        about = Gtk.Label(
            label=f"NC-Claw v{VERSION}\nGTK4 + Libadwaita · Custom Commands · Markdown",
        )
        about.add_css_class("dim-label")
        about.set_justify(Gtk.Justification.CENTER)
        about.set_margin_top(24)
        content.append(about)

        clamp = Adw.Clamp(maximum_size=640, tightening_threshold=400)
        clamp.set_child(content)
        scroll.set_child(clamp)
        self.append(scroll)

    def _on_save(self, _):
        try:
            mt = int(self.tokens_row.get_text())
        except ValueError:
            mt = DEFAULT_CONFIG["max_tokens"]
        ps, pe = self.prompt_buf.get_bounds()
        self.config.update({
            "api_endpoint": self.ep_row.get_text(),
            "api_key": self.key_row.get_text(),
            "model": self.model_row.get_text(),
            "max_tokens": mt,
            "temperature": self.temp_scale.get_value(),
            "confirm_execution": self.confirm_row.get_active(),
            "system_prompt": self.prompt_buf.get_text(ps, pe, False),
        })
        save_config(self.config)
        toast = Adw.Toast.new("Settings saved!")
        toast.set_timeout(2)
        self.toast_overlay.add_toast(toast)


# ═══════════════════════════════════════════════════════════
# Main Window
# ═══════════════════════════════════════════════════════════

class MainWindow(Adw.ApplicationWindow):

    def __init__(self, app):
        super().__init__(application=app)
        self.set_title(APP_TITLE)
        self.set_default_size(1200, 750)

        toast = Adw.ToastOverlay()
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        self.hdr_title = Gtk.Label(label="Chat")
        self.hdr_title.add_css_class("heading")
        header.set_title_widget(self.hdr_title)
        root.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.set_size_request(200, -1)

        brand = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        brand.set_margin_top(16)
        brand.set_margin_bottom(8)
        brand.set_margin_start(16)
        brand.set_margin_end(16)
        name = Gtk.Label(label=APP_TITLE)
        name.add_css_class("title-3")
        name.set_xalign(0)
        brand.append(name)
        ver = Gtk.Label(label=f"v{VERSION}")
        ver.add_css_class("dim-label")
        ver.add_css_class("caption")
        ver.set_xalign(0)
        brand.append(ver)
        sidebar.append(brand)
        sidebar.append(Gtk.Separator())

        self.nav = Gtk.ListBox()
        self.nav.add_css_class("navigation-sidebar")
        self.nav.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav.connect("row-selected", self._on_nav)

        for label, icon in [
            ("Chat", "accessories-text-editor-symbolic"),
            ("Commands", "utilities-terminal-symbolic"),
            ("API Editor", "text-html-symbolic"),
            ("Settings", "preferences-system-symbolic"),
        ]:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.set_margin_top(10)
            box.set_margin_bottom(10)
            box.set_margin_start(12)
            box.set_margin_end(12)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(18)
            box.append(img)
            lbl = Gtk.Label(label=label, xalign=0)
            lbl.set_hexpand(True)
            box.append(lbl)
            row.set_child(box)
            self.nav.append(row)

        sidebar.append(self.nav)
        body.append(sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(200)
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)

        self.stack.add_named(ChatPage(self), "chat")
        self.stack.add_named(CommandsPage(), "commands")
        self.stack.add_named(APIEditorPage(), "api")
        self.stack.add_named(SettingsPage(toast), "settings")

        body.append(self.stack)
        root.append(body)

        toast.set_child(root)
        self.set_content(toast)

        first = self.nav.get_row_at_index(0)
        if first:
            self.nav.select_row(first)

    def _on_nav(self, _lb, row):
        if row is None:
            return
        ids = ["chat", "commands", "api", "settings"]
        titles = ["Chat", "Command Runner", "API Editor", "Settings"]
        idx = row.get_index()
        if idx < len(ids):
            self.stack.set_visible_child_name(ids[idx])
            self.hdr_title.set_text(titles[idx])


# ═══════════════════════════════════════════════════════════
# Application
# ═══════════════════════════════════════════════════════════

class NCClawApp(Adw.Application):

    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_FILE.exists():
            save_config(DEFAULT_CONFIG)
        win = MainWindow(app)
        win.present()


def main():
    app = NCClawApp()
    app.run()

if __name__ == "__main__":
    main()


#thanks for watching
