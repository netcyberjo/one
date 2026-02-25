import flet as ft
import requests
import threading
import sqlite3
import time
import json

# ==========================================
# آدرس ورکر کلودفلر خود را در خط زیر قرار دهید
WORKER_URL = "https://b.neke.workers.dev"
# ==========================================

class TelegramProApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "Telegram Pro"
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.window_width = 400
        self.page.window_height = 700
        self.page.rtl = True # راست‌چین کردن کل برنامه
        
        self.db_lock = threading.Lock()
        # استفاده از مسیر نسبی برای دیتابیس
        self.conn = sqlite3.connect("telegram_flet.db", check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.setup_databases()

        self.username = None
        self.is_running = True
        self.current_chat = "global"

        self.build_login_view()

    def setup_databases(self):
        """ایجاد جداول مورد نیاز در صورت عدم وجود"""
        with self.db_lock:
            self.cursor.execute('CREATE TABLE IF NOT EXISTS processed_events (id TEXT PRIMARY KEY)')
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, username TEXT, recipient TEXT, text TEXT, 
                    reply_to TEXT, timestamp TEXT, is_edited INTEGER DEFAULT 0
                )
            ''')
            self.cursor.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, is_admin INTEGER)''')
            self.cursor.execute('''CREATE TABLE IF NOT EXISTS known_users (username TEXT PRIMARY KEY, last_seen REAL)''')
            
            # ایجاد اکانت ادمین پیش‌فرض
            self.cursor.execute("SELECT * FROM users WHERE username='admin'")
            if not self.cursor.fetchone():
                self.cursor.execute("INSERT INTO users (username, password, is_admin) VALUES ('admin', 'admin123', 1)")
            self.conn.commit()

    def update_last_seen(self, username):
        with self.db_lock:
            self.cursor.execute("INSERT OR REPLACE INTO known_users (username, last_seen) VALUES (?, ?)", (username, time.time()))
            self.conn.commit()

    def show_snack(self, message, color=ft.Colors.BLUE):
        """نمایش پیام موقت به کاربر (سازگار با تمامی نسخه‌ها)"""
        snack = ft.SnackBar(ft.Text(message), bgcolor=color)
        self.page.snack_bar = snack
        snack.open = True
        self.page.update()

    def build_login_view(self):
        self.page.clean()
        self.page.vertical_alignment = ft.MainAxisAlignment.CENTER
        self.page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        
        self.user_input = ft.TextField(label="نام کاربری", prefix_icon=ft.Icons.PERSON, width=300)
        self.pass_input = ft.TextField(label="رمز عبور", prefix_icon=ft.Icons.LOCK, password=True, can_reveal_password=True, width=300)
        
        def attempt_login(e):
            u = self.user_input.value.strip()
            p = self.pass_input.value.strip()
            if not u or not p:
                self.show_snack("لطفاً اطلاعات را کامل کنید", ft.Colors.RED)
                return

            with self.db_lock:
                self.cursor.execute("SELECT is_admin FROM users WHERE username=? AND password=?", (u, p))
                user_data = self.cursor.fetchone()

            if user_data:
                self.username = u
                self.update_last_seen(u)
                self.build_main_view()
            else:
                self.show_snack("نام کاربری یا رمز عبور اشتباه است", ft.Colors.RED)

        self.page.add(
            ft.Icon(ft.Icons.TELEGRAM, size=80, color=ft.Colors.BLUE),
            ft.Text("Telegram Pro", size=30, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE),
            ft.Text("خوش آمدید! لطفاً وارد شوید.", size=14, color=ft.Colors.GREY_700),
            ft.Container(height=20),
            self.user_input,
            self.pass_input,
            ft.Container(height=10),
            ft.FilledButton("ورود به حساب", on_click=attempt_login, width=300, bgcolor=ft.Colors.BLUE, color=ft.Colors.WHITE)
        )

    def build_main_view(self):
        self.page.clean()
        self.page.vertical_alignment = ft.MainAxisAlignment.START
        self.page.horizontal_alignment = ft.CrossAxisAlignment.START
        
        # بخش پیام‌ها
        self.chat_list = ft.ListView(expand=True, spacing=10, auto_scroll=True, padding=15)
        self.msg_input = ft.TextField(
            hint_text="پیام خود را بنویسید...",
            expand=True,
            border_radius=20,
            on_submit=self.send_message
        )
        
        # سایدبار
        self.drawer = ft.NavigationDrawer(
            on_change=self.drawer_changed,
            controls=[
                ft.Container(height=12),
                ft.Column([
                    ft.CircleAvatar(content=ft.Icon(ft.Icons.PERSON), radius=30),
                    ft.Text(f"{self.username}", weight="bold", size=16),
                ], alignment="center", horizontal_alignment="center"),
                ft.Divider(thickness=1),
                ft.NavigationDrawerDestination(icon=ft.Icons.PUBLIC, label="اتاق عمومی"),
            ]
        )

        # نوار ابزار بالا
        self.page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.Icons.MENU, on_click=self.open_drawer_action),
            title=ft.Text("اتاق عمومی"),
            center_title=True,
            bgcolor=ft.Colors.SURFACE_CONTAINER,
            actions=[ft.IconButton(ft.Icons.BRIGHTNESS_4, on_click=self.toggle_theme)]
        )

        self.page.drawer = self.drawer
        self.page.add(
            self.chat_list,
            ft.Container(
                content=ft.Row([
                    self.msg_input,
                    ft.IconButton(ft.Icons.SEND, icon_color=ft.Colors.BLUE, on_click=self.send_message),
                ]),
                padding=10
            )
        )
        
        self.refresh_drawer()
        self.render_chat_history()
        
        # شروع ترد دریافت پیام با امنیت بیشتر
        threading.Thread(target=self.poll_messages_loop, daemon=True).start()

    def open_drawer_action(self, e):
        """باز کردن منو"""
        self.drawer.open = True
        self.page.update()

    def toggle_theme(self, e):
        self.page.theme_mode = ft.ThemeMode.DARK if self.page.theme_mode == ft.ThemeMode.LIGHT else ft.ThemeMode.LIGHT
        self.page.update()

    def refresh_drawer(self):
        """به‌روزرسانی لیست کاربران در سایدبار"""
        # نگهداری آیتم‌های ثابت (هدر و اتاق عمومی)
        base_controls = self.drawer.controls[:4] 
        with self.db_lock:
            self.cursor.execute("SELECT username FROM known_users WHERE username != ? ORDER BY last_seen DESC", (self.username,))
            users = self.cursor.fetchall()
            
        for row in users:
            base_controls.append(ft.NavigationDrawerDestination(icon=ft.Icons.PERSON_OUTLINE, label=row[0]))
        
        self.drawer.controls = base_controls
        self.page.update()

    def drawer_changed(self, e):
        """مدیریت تغییر چت از طریق منو"""
        # پیدا کردن مقصد انتخاب شده بر اساس ایندکس
        try:
            dest_index = int(e.data)
            # استخراج تمام مقاصد (Destinations) از لیست کنترلها
            all_destinations = [c for c in self.drawer.controls if isinstance(c, ft.NavigationDrawerDestination)]
            if dest_index < len(all_destinations):
                selected_label = all_destinations[dest_index].label
                if selected_label == "اتاق عمومی":
                    self.switch_chat("global")
                else:
                    self.switch_chat(selected_label)
        except:
            pass
            
        self.drawer.open = False
        self.page.update()

    def switch_chat(self, target):
        self.current_chat = target
        self.page.appbar.title.value = "اتاق عمومی" if target == "global" else f"چت با {target}"
        self.render_chat_history()

    def render_chat_history(self):
        self.chat_list.controls.clear()
        if self.current_chat == "global":
            query = "SELECT id, username, text, timestamp, is_edited FROM messages WHERE recipient='global' ORDER BY timestamp ASC"
            params = []
        else:
            query = "SELECT id, username, text, timestamp, is_edited FROM messages WHERE ((recipient=? AND username=?) OR (recipient=? AND username=?)) ORDER BY timestamp ASC"
            params = [self.current_chat, self.username, self.username, self.current_chat]
            
        with self.db_lock:
            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()
            
        for row in rows:
            self.add_bubble_to_ui(row[0], row[1], row[2], row[3], row[4])
        self.page.update()

    def add_bubble_to_ui(self, msg_id, sender, text, timestamp, is_edited):
        is_me = (sender == self.username)
        bubble_color = ft.Colors.BLUE_100 if is_me else ft.Colors.GREY_200
        align = ft.MainAxisAlignment.END if is_me else ft.MainAxisAlignment.START

        # جلوگیری از نمایش پیام‌های تکراری در UI (اگر قبلاً به صورت خوش‌بینانه اضافه شده باشد)
        for control in self.chat_list.controls:
            if hasattr(control, "key") and control.key == msg_id:
                return

        bubble = ft.Container(
            key=msg_id,
            content=ft.Column([
                ft.Text(sender if not is_me else "شما", weight="bold", size=10, color=ft.Colors.BLUE_700),
                ft.Text(text, size=14),
                ft.Text(timestamp[-5:] if timestamp else "الان", size=9, color=ft.Colors.GREY_600),
            ], spacing=2),
            bgcolor=bubble_color,
            border_radius=ft.BorderRadius.all(10),
            padding=10,
            width=280, # استفاده از عرض ثابت برای سازگاری کامل با نسخه‌های قدیمی Thonny
        )
        self.chat_list.controls.append(ft.Row([bubble], alignment=align))

    def poll_messages_loop(self):
        """حلقه دریافت پیام از سرور"""
        while self.is_running:
            try:
                resp = requests.get(WORKER_URL, timeout=5)
                if resp.status_code == 200:
                    events = resp.json()
                    new_messages = False
                    
                    with self.db_lock:
                        for ev in events:
                            ev_id = ev.get('id')
                            self.cursor.execute("SELECT id FROM processed_events WHERE id=?", (ev_id,))
                            if not self.cursor.fetchone():
                                if ev.get('action') == "message":
                                    d = ev.get('data', {})
                                    self.cursor.execute(
                                        'INSERT INTO messages (id, username, recipient, text, timestamp) VALUES (?, ?, ?, ?, ?)',
                                        (d.get('id'), ev.get('username'), d.get('to', 'global'), d.get('text'), ev.get('timestamp'))
                                    )
                                    self.update_last_seen(ev.get('username'))
                                    new_messages = True
                                self.cursor.execute("INSERT INTO processed_events (id) VALUES (?)", (ev_id,))
                        self.conn.commit()
                    
                    if new_messages:
                        self.render_chat_history()
                        
            except Exception as ex:
                pass
            time.sleep(3)

    def send_message(self, e=None):
        text = self.msg_input.value.strip()
        if not text: return
            
        # ۱. پاک کردن فیلد ورودی بلافاصله برای حس سرعت
        self.msg_input.value = ""
        
        # ۲. تولید آیدی موقت و زمان
        msg_id = str(int(time.time() * 1000))
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        
        # ۳. نمایش پیام در UI خودمان بلافاصله (Optimistic Update)
        self.add_bubble_to_ui(msg_id, self.username, text, ts, 0)
        self.page.update()

        # ۴. ارسال به سرور در پس‌زمینه
        payload = {
            "username": self.username,
            "action": "message",
            "data": {"id": msg_id, "text": text, "to": self.current_chat}
        }
        
        def _post():
            try:
                r = requests.post(WORKER_URL, json=payload, timeout=10)
                if r.status_code != 200:
                    # اگر ارسال نشد، به کاربر اطلاع بده (اختیاری)
                    pass
            except:
                pass
            
        threading.Thread(target=_post, daemon=True).start()

def main(page: ft.Page):
    app = TelegramProApp(page)

if __name__ == "__main__":
    ft.run(main)