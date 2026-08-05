# -*- coding: utf-8 -*-
"""
tomacro - 클립스튜디오 ↔ 클립스튜디오 모델러 텍스처 반복 작업용 매크로 프로그램

핵심 동작:
  - 마우스/키보드 입력을 녹화하고 재생한다 (타이니태스크 방식).
  - 녹화 중 Alt+Tab 으로 창을 전환하면, 키 입력 대신 "어느 창으로 전환했는지"를 기록한다.
  - 재생할 때는 Alt+Tab 을 누르는 대신 Windows API 로 해당 창을 직접 활성화한다.
    → 창 순서가 바뀌어도 항상 올바른 프로그램(클립스튜디오/모델러)으로 전환된다.

단축키:
  F9  : 녹화 시작 / 녹화 중지
  F10 : 재생 시작
  ESC : 재생 중지
"""

import ctypes
import ctypes.wintypes as wintypes
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from pynput import keyboard, mouse
from pynput.keyboard import Key, KeyCode

# ---------------------------------------------------------------- Win32 헬퍼

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [POINT]

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]

GA_ROOT = 2
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 작업 전환기(Alt+Tab UI) 등 무시할 시스템 창 클래스
IGNORE_CLASSES = {
    "MultitaskingViewFrame",
    "XamlExplorerHostIslandWindow",
    "ForegroundStaging",
    "Windows.UI.Core.CoreWindow",
    "TaskSwitcherWnd",
    "Shell_TrayWnd",
}


def win_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def win_pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def win_exe(hwnd):
    """창을 소유한 프로세스의 실행 파일 이름 (예: CLIPStudioPaint.exe)"""
    pid = win_pid(hwnd)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def enum_visible_windows():
    result = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and win_title(hwnd):
            result.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return result


def find_window(exe, title, cls):
    """녹화된 창 정보(실행파일/제목/클래스)와 가장 잘 맞는 창 핸들을 찾는다."""
    best, best_score = None, -1
    for strict_exe in (True, False):
        for hwnd in enum_visible_windows():
            c = win_class(hwnd)
            if c in IGNORE_CLASSES:
                continue
            e = win_exe(hwnd)
            if strict_exe:
                if not exe or e.lower() != exe.lower():
                    continue
                score = 10
            else:
                score = 0
            t = win_title(hwnd)
            if title and t == title:
                score += 4
            elif title and t and (title in t or t in title):
                score += 2
            if cls and c == cls:
                score += 1
            if score > best_score:
                best, best_score = hwnd, score
        if best is not None:
            return best
    return None


def activate_window(hwnd, timeout=3.0):
    """창을 전면으로 가져오고, 실제로 활성화될 때까지 기다린다."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    def try_activate():
        fg = user32.GetForegroundWindow()
        cur_tid = kernel32.GetCurrentThreadId()
        tgt_tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wintypes.DWORD()))
        fg_tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(wintypes.DWORD())) if fg else 0
        attached = []
        for tid in {tgt_tid, fg_tid} - {0, cur_tid}:
            if user32.AttachThreadInput(cur_tid, tid, True):
                attached.append(tid)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        for tid in attached:
            user32.AttachThreadInput(cur_tid, tid, False)

    deadline = time.time() + timeout
    try_activate()
    while time.time() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        # 포그라운드 잠금 해제 트릭: Alt 키를 한 번 눌렀다 떼고 재시도
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        try_activate()
        time.sleep(0.1)
    return user32.GetForegroundWindow() == hwnd


# ------------------------------------------------------------- 키 직렬화

def key_to_dict(key):
    if isinstance(key, Key):
        return {"kind": "special", "name": key.name}
    if isinstance(key, KeyCode):
        d = {"kind": "code"}
        if key.vk is not None:
            d["vk"] = key.vk
        if key.char is not None:
            d["char"] = key.char
        return d
    return None


def dict_to_key(d):
    if d["kind"] == "special":
        return getattr(Key, d["name"], None)
    if "vk" in d:
        return KeyCode.from_vk(d["vk"])
    return KeyCode.from_char(d["char"])


ALT_NAMES = {"alt", "alt_l", "alt_r", "alt_gr"}
SHIFT_NAMES = {"shift", "shift_l", "shift_r"}
CONTROL_KEYS = {Key.f9, Key.f10, Key.esc}  # 프로그램 조작용 키는 녹화 제외


def scrub_alt_tab(events):
    """Alt+Tab 키 시퀀스를 제거한다. (창 전환은 focus 이벤트가 대신 수행)"""
    out = []
    i, n = 0, len(events)
    while i < n:
        e = events[i]
        if (
            e["type"] == "key_down"
            and e["key"]["kind"] == "special"
            and e["key"]["name"] in ALT_NAMES
        ):
            j = i + 1
            contains_tab = False
            while j < n:
                e2 = events[j]
                if e2["type"] in ("key_down", "key_up") and e2["key"]["kind"] == "special":
                    nm = e2["key"]["name"]
                    if nm == "tab" and e2["type"] == "key_down":
                        contains_tab = True
                    if nm in ALT_NAMES and e2["type"] == "key_up":
                        break
                j += 1
            if contains_tab:
                for k in range(i + 1, min(j, n)):
                    e2 = events[k]
                    if e2["type"] in ("key_down", "key_up") and e2["key"]["kind"] == "special":
                        if e2["key"]["name"] in ALT_NAMES | SHIFT_NAMES | {"tab"}:
                            continue
                    out.append(e2)
                i = j + 1
                continue
        out.append(e)
        i += 1
    return out


# ---------------------------------------------------------------- 녹화기

class Recorder:
    MOVE_INTERVAL = 0.02  # 마우스 이동 기록 간격(초)

    def __init__(self, own_pid):
        self.own_pid = own_pid
        self.events = []
        self.recording = False
        self._t0 = 0.0
        self._last_move = 0.0
        self._m_listener = None
        self._k_listener = None
        self._focus_stop = threading.Event()

    def _now(self):
        return time.time() - self._t0

    def _is_own_window_at(self, x, y):
        hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
        if not hwnd:
            return False
        root = user32.GetAncestor(hwnd, GA_ROOT)
        return win_pid(root) == self.own_pid

    def start(self):
        self.events = []
        self._t0 = time.time()
        self._last_move = 0.0
        self.recording = True

        # 시작 시점의 활성 창을 첫 focus 이벤트로 기록
        fg = user32.GetForegroundWindow()
        if fg and win_pid(fg) != self.own_pid and win_class(fg) not in IGNORE_CLASSES:
            self._add_focus(fg, 0.0)

        self._focus_stop.clear()
        threading.Thread(target=self._focus_poll, daemon=True).start()

        self._m_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll
        )
        self._k_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._m_listener.start()
        self._k_listener.start()

    def stop(self):
        self.recording = False
        self._focus_stop.set()
        if self._m_listener:
            self._m_listener.stop()
        if self._k_listener:
            self._k_listener.stop()
        self.events = scrub_alt_tab(self.events)
        return self.events

    # --- focus 폴링: 활성 창이 바뀌면 기록 ---
    def _add_focus(self, hwnd, t):
        self.events.append(
            {
                "t": t,
                "type": "focus",
                "exe": win_exe(hwnd),
                "title": win_title(hwnd),
                "class": win_class(hwnd),
            }
        )

    def _focus_poll(self):
        last = user32.GetForegroundWindow()
        while not self._focus_stop.is_set():
            time.sleep(0.05)
            fg = user32.GetForegroundWindow()
            if fg and fg != last:
                last = fg
                if win_pid(fg) == self.own_pid:
                    continue
                if win_class(fg) in IGNORE_CLASSES:
                    continue
                self._add_focus(fg, self._now())

    # --- 마우스 ---
    def _on_move(self, x, y):
        if not self.recording:
            return
        t = self._now()
        if t - self._last_move < self.MOVE_INTERVAL:
            return
        self._last_move = t
        self.events.append({"t": t, "type": "move", "x": x, "y": y})

    def _on_click(self, x, y, button, pressed):
        if not self.recording:
            return
        if self._is_own_window_at(x, y):
            return
        self.events.append(
            {
                "t": self._now(),
                "type": "click",
                "x": x,
                "y": y,
                "button": button.name,
                "pressed": pressed,
            }
        )

    def _on_scroll(self, x, y, dx, dy):
        if not self.recording:
            return
        if self._is_own_window_at(x, y):
            return
        self.events.append(
            {"t": self._now(), "type": "scroll", "x": x, "y": y, "dx": dx, "dy": dy}
        )

    # --- 키보드 ---
    def _on_press(self, key):
        if not self.recording or key in CONTROL_KEYS:
            return
        d = key_to_dict(key)
        if d:
            self.events.append({"t": self._now(), "type": "key_down", "key": d})

    def _on_release(self, key):
        if not self.recording or key in CONTROL_KEYS:
            return
        d = key_to_dict(key)
        if d:
            self.events.append({"t": self._now(), "type": "key_up", "key": d})


# ---------------------------------------------------------------- 재생기

class Player:
    def __init__(self):
        self.playing = False
        self.stop_flag = threading.Event()
        self.progress = ""  # GUI 표시용
        self._mouse = mouse.Controller()
        self._kbd = keyboard.Controller()

    def stop(self):
        self.stop_flag.set()

    def play(self, events, repeat=1, speed=1.0, gap=1.0, on_done=None):
        if self.playing or not events:
            return
        self.playing = True
        self.stop_flag.clear()
        threading.Thread(
            target=self._run, args=(events, repeat, speed, gap, on_done), daemon=True
        ).start()

    def _run(self, events, repeat, speed, gap, on_done):
        try:
            for r in range(repeat):
                if self.stop_flag.is_set():
                    break
                self.progress = f"{r + 1}/{repeat}"
                self._play_once(events, speed)
                if self.stop_flag.is_set():
                    break
                if r < repeat - 1 and gap > 0:
                    self._sleep(gap)
        finally:
            self._release_all(events)
            self.playing = False
            self.progress = ""
            if on_done:
                on_done()

    def _sleep(self, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.stop_flag.is_set():
                return
            time.sleep(0.02)

    def _play_once(self, events, speed):
        t0 = events[0]["t"]
        start = time.time()
        for e in events:
            if self.stop_flag.is_set():
                return
            target = start + (e["t"] - t0) / speed
            while True:
                remain = target - time.time()
                if remain <= 0:
                    break
                if self.stop_flag.is_set():
                    return
                time.sleep(min(remain, 0.02))

            et = e["type"]
            if et == "focus":
                before = time.time()
                hwnd = find_window(e.get("exe", ""), e.get("title", ""), e.get("class", ""))
                if hwnd:
                    activate_window(hwnd)
                    time.sleep(0.15)  # 창 전환 후 안정화 대기
                # 창 전환에 걸린 시간만큼 일정을 뒤로 민다
                start += time.time() - before
            elif et == "move":
                self._mouse.position = (e["x"], e["y"])
            elif et == "click":
                self._mouse.position = (e["x"], e["y"])
                btn = getattr(mouse.Button, e["button"], mouse.Button.left)
                if e["pressed"]:
                    self._mouse.press(btn)
                else:
                    self._mouse.release(btn)
            elif et == "scroll":
                self._mouse.position = (e["x"], e["y"])
                self._mouse.scroll(e["dx"], e["dy"])
            elif et == "key_down":
                k = dict_to_key(e["key"])
                if k:
                    try:
                        self._kbd.press(k)
                    except Exception:
                        pass
            elif et == "key_up":
                k = dict_to_key(e["key"])
                if k:
                    try:
                        self._kbd.release(k)
                    except Exception:
                        pass

    def _release_all(self, events):
        """재생이 중간에 끊겨도 눌린 키/버튼이 남지 않게 전부 뗀다."""
        for e in events:
            try:
                if e["type"] == "key_down":
                    k = dict_to_key(e["key"])
                    if k:
                        self._kbd.release(k)
                elif e["type"] == "click" and e["pressed"]:
                    btn = getattr(mouse.Button, e["button"], mouse.Button.left)
                    self._mouse.release(btn)
            except Exception:
                pass


# ---------------------------------------------------------------- GUI

# exe(PyInstaller)로 실행되면 exe 옆에, 스크립트로 실행되면 스크립트 옆에 저장
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_DIR = os.path.join(_BASE_DIR, "macros")


def resource_path(name):
    """번들 리소스 경로 (PyInstaller onefile은 임시 폴더에 압축 해제됨)"""
    return os.path.join(getattr(sys, "_MEIPASS", _BASE_DIR), name)


class App:
    def __init__(self, root):
        self.root = root
        self.recorder = Recorder(os.getpid())
        self.player = Player()
        self.events = []  # 현재 로드/녹화된 매크로

        os.makedirs(MACRO_DIR, exist_ok=True)

        root.title("tomacro - 클립스튜디오 매크로")
        root.geometry("360x520")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        self.status_var = tk.StringVar(value="대기 중")
        status = tk.Label(
            root, textvariable=self.status_var, font=("맑은 고딕", 14, "bold"),
            fg="#333", pady=8,
        )
        status.pack(fill="x")
        self.status_label = status

        info = tk.Label(
            root,
            text="F9: 녹화 시작/중지    F10: 재생    ESC: 재생 중지\n"
            "Alt+Tab 창 전환도 자동으로 녹화·재생됩니다",
            font=("맑은 고딕", 9), fg="#666",
        )
        info.pack()

        btns = tk.Frame(root, pady=6)
        btns.pack()
        self.btn_rec = tk.Button(btns, text="● 녹화 (F9)", width=12, command=self.toggle_record)
        self.btn_play = tk.Button(btns, text="▶ 재생 (F10)", width=12, command=self.play)
        self.btn_stop = tk.Button(btns, text="■ 중지 (ESC)", width=12, command=self.stop_play)
        self.btn_rec.grid(row=0, column=0, padx=3)
        self.btn_play.grid(row=0, column=1, padx=3)
        self.btn_stop.grid(row=0, column=2, padx=3)

        opts = tk.LabelFrame(root, text="재생 설정", font=("맑은 고딕", 9), padx=8, pady=6)
        opts.pack(fill="x", padx=10, pady=6)

        tk.Label(opts, text="반복 횟수", font=("맑은 고딕", 9)).grid(row=0, column=0, sticky="w")
        self.repeat_var = tk.IntVar(value=1)
        tk.Spinbox(opts, from_=1, to=999, width=6, textvariable=self.repeat_var).grid(row=0, column=1, padx=6)

        tk.Label(opts, text="반복 간격(초)", font=("맑은 고딕", 9)).grid(row=0, column=2, sticky="w")
        self.gap_var = tk.DoubleVar(value=1.0)
        tk.Spinbox(opts, from_=0, to=60, increment=0.5, width=6, textvariable=self.gap_var).grid(row=0, column=3, padx=6)

        tk.Label(opts, text="재생 속도", font=("맑은 고딕", 9)).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.speed_var = tk.StringVar(value="1.0")
        ttk.Combobox(
            opts, textvariable=self.speed_var, width=5, state="readonly",
            values=("0.5", "0.8", "1.0", "1.5", "2.0", "3.0"),
        ).grid(row=1, column=1, padx=6, pady=(6, 0))

        files = tk.LabelFrame(root, text="저장된 매크로", font=("맑은 고딕", 9), padx=8, pady=6)
        files.pack(fill="both", expand=True, padx=10, pady=6)

        self.listbox = tk.Listbox(files, font=("맑은 고딕", 10), height=8)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda e: self.load_selected())

        fb = tk.Frame(files, pady=4)
        fb.pack()
        tk.Button(fb, text="현재 녹화 저장", command=self.save_macro).grid(row=0, column=0, padx=3)
        tk.Button(fb, text="불러오기", command=self.load_selected).grid(row=0, column=1, padx=3)
        tk.Button(fb, text="삭제", command=self.delete_selected).grid(row=0, column=2, padx=3)

        self.count_var = tk.StringVar(value="현재 매크로: 없음")
        tk.Label(root, textvariable=self.count_var, font=("맑은 고딕", 9), fg="#666").pack(pady=(0, 6))

        self.refresh_list()

        # 전역 단축키 (녹화용 리스너와 별개로 항상 동작)
        self.hotkeys = keyboard.Listener(on_press=self._on_hotkey)
        self.hotkeys.start()

        self._tick()

    # --- 단축키 ---
    def _on_hotkey(self, key):
        if key == Key.f9:
            self.root.after(0, self.toggle_record)
        elif key == Key.f10:
            self.root.after(0, self.play)
        elif key == Key.esc:
            self.player.stop()

    # --- 녹화 ---
    def toggle_record(self):
        if self.player.playing:
            return
        if not self.recorder.recording:
            self.recorder.start()
        else:
            self.events = self.recorder.stop()
            self.count_var.set(f"현재 매크로: 새 녹화 ({len(self.events)}개 동작)")

    # --- 재생 ---
    def play(self):
        if self.recorder.recording or self.player.playing:
            return
        if not self.events:
            messagebox.showinfo("tomacro", "재생할 매크로가 없습니다.\n먼저 녹화하거나 저장된 매크로를 불러오세요.")
            return
        try:
            repeat = max(1, int(self.repeat_var.get()))
            gap = max(0.0, float(self.gap_var.get()))
            speed = float(self.speed_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("tomacro", "반복 횟수/간격/속도 값을 확인하세요.")
            return
        self.player.play(self.events, repeat=repeat, speed=speed, gap=gap)

    def stop_play(self):
        self.player.stop()

    # --- 파일 ---
    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        for f in sorted(os.listdir(MACRO_DIR)):
            if f.endswith(".json"):
                self.listbox.insert(tk.END, f[:-5])

    # 주의: macros/*.json 은 사용자의 자산이므로 형식 하위 호환을 반드시 유지할 것.
    # 필드 변경/삭제 금지, 추가만 허용. 형식 변경 시 version 올리고 마이그레이션 필수.
    # (자세한 규칙은 README "매크로 파일 하위 호환성" 참고)
    def save_macro(self):
        if not self.events:
            messagebox.showinfo("tomacro", "저장할 녹화가 없습니다. 먼저 F9로 녹화하세요.")
            return
        name = simpledialog.askstring(
            "매크로 저장", "매크로 이름 (예: 메테리얼2개, 기본텍스처):", parent=self.root
        )
        if not name:
            return
        name = "".join(c for c in name if c not in '\\/:*?"<>|').strip()
        if not name:
            return
        path = os.path.join(MACRO_DIR, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "events": self.events}, f, ensure_ascii=False)
        self.refresh_list()
        self.count_var.set(f"현재 매크로: {name} ({len(self.events)}개 동작)")

    def _selected_name(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("tomacro", "목록에서 매크로를 선택하세요.")
            return None
        return self.listbox.get(sel[0])

    def load_selected(self):
        name = self._selected_name()
        if not name:
            return
        path = os.path.join(MACRO_DIR, name + ".json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.events = data["events"]
            self.count_var.set(f"현재 매크로: {name} ({len(self.events)}개 동작)")
        except Exception as e:
            messagebox.showerror("tomacro", f"불러오기 실패: {e}")

    def delete_selected(self):
        name = self._selected_name()
        if not name:
            return
        if messagebox.askyesno("tomacro", f"'{name}' 매크로를 삭제할까요?"):
            os.remove(os.path.join(MACRO_DIR, name + ".json"))
            self.refresh_list()

    # --- 상태 표시 갱신 ---
    def _tick(self):
        if self.recorder.recording:
            self.status_var.set(f"● 녹화 중... ({len(self.recorder.events)}개)  F9로 중지")
            self.status_label.config(fg="#c0392b")
        elif self.player.playing:
            self.status_var.set(f"▶ 재생 중... ({self.player.progress})  ESC로 중지")
            self.status_label.config(fg="#27ae60")
        else:
            self.status_var.set("대기 중")
            self.status_label.config(fg="#333")
        self.root.after(100, self._tick)


def main():
    # DPI 인식: 화면 배율(125% 등)이 켜져 있어도 좌표가 어긋나지 않게 함
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    try:
        root.iconbitmap(resource_path("icon.ico"))
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
