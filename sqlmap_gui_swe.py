import sys
import subprocess
import platform
import shlex
import os
import json
import shutil # Needed for shutil.which

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTabWidget, QLabel, QLineEdit, QCheckBox, QFileDialog, QMessageBox,
    QScrollArea, QFormLayout, QSpinBox, QGroupBox, QTextEdit, QComboBox,
    QMenuBar, QStatusBar, QDialog, QDialogButtonBox, QLayout, QStyleFactory
)
from PySide6.QtGui import QAction, QKeySequence, QTextCursor, QFont, QIcon, QGuiApplication
from PySide6.QtCore import Qt, Slot, QThread, Signal, QSettings, QSize

# --- Hjälpfunktioner för Widgets ---

def create_file_input(label_text, parent_widget, is_directory=False):
    """
    Creates a standard horizontal layout with a label, line edit, and browse button.
    Connects the button's clicked signal to the appropriate file/directory selection slot in the parent.
    """
    label = QLabel(label_text)
    line_edit = QLineEdit()
    button = QPushButton("Bläddra...")
    widget = QWidget() # Use a container widget for layout
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0,0,0,0) # No margins within this specific layout
    layout.addWidget(label)
    layout.addWidget(line_edit)
    layout.addWidget(button)

    # Connect the button click to the appropriate slot in the parent widget
    if is_directory:
        button.clicked.connect(lambda checked=False, le=line_edit: parent_widget.select_directory(le))
    else:
        button.clicked.connect(lambda checked=False, le=line_edit: parent_widget.select_file(le))

    return widget, line_edit

# --- Worker Thread for running sqlmap ---

class SqlmapRunnerThread(QThread):
    """
    Runs the sqlmap command in a separate thread to avoid freezing the GUI.
    Emits signals for output text, process completion, and errors.
    Includes an identifier to link back to the correct output tab.
    """
    outputTextAvailable = Signal(int, str)
    processFinished = Signal(int, int)
    processError = Signal(int, str)
    thread_counter = 0

    def __init__(self, command_list, parent=None):
        super().__init__(parent)
        self.command_list = command_list
        self.process = None
        self._is_running = True
        self.identifier = SqlmapRunnerThread.thread_counter
        SqlmapRunnerThread.thread_counter += 1

    def run(self):
        self._is_running = True
        instance_id = self.identifier
        try:
            # --batch is now handled by the checkbox in the GUI and build_sqlmap_command
            # No automatic addition here anymore.
            # Warning for interactive flags without external window/batch is handled before thread start.
            
            self.outputTextAvailable.emit(instance_id, f"[KOMMANDO] Kör: {' '.join(map(str,self.command_list))}\n---\n")

            self.process = subprocess.Popen(
                self.command_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
            )

            while self._is_running and self.process.stdout:
                line = self.process.stdout.readline()
                if line:
                    self.outputTextAvailable.emit(instance_id, line)
                elif self.process.poll() is not None:
                    break
                else:
                    QThread.msleep(50)

            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                self.outputTextAvailable.emit(instance_id, "\n[INFO] Process avbruten av användaren.\n")

            return_code = self.process.wait() if self.process else -1
            self.processFinished.emit(instance_id, return_code)

        except FileNotFoundError:
            self.processError.emit(instance_id, f"Fel: Kommandot '{self.command_list[0]}' hittades inte. Kontrollera sökvägen i Arkiv -> Välj sqlmap Sökväg.")
            self.processFinished.emit(instance_id, -1)
        except Exception as e:
            self.processError.emit(instance_id, f"Ett oväntat fel uppstod i körningstråden: {e}")
            self.processFinished.emit(instance_id, -1)
        finally:
            self.process = None
            self._is_running = False

    def stop(self):
        instance_id = self.identifier
        self._is_running = False
        if self.process and self.process.poll() is None:
            self.outputTextAvailable.emit(instance_id, "\n[INFO] Försöker stoppa processen...\n")
            try:
                self.process.terminate()
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                 self.outputTextAvailable.emit(instance_id, "[INFO] Process svarade inte på terminate, försöker kill...\n")
                 self.process.kill()
            except Exception as e:
                 self.outputTextAvailable.emit(instance_id, f"[VARNING] Fel vid försök att stoppa processen: {e}\n")

# --- Main GUI Class ---
class SqlmapGui(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sqlmap GUI Wrapper")
        self.setGeometry(100, 100, 1200, 800)
        self.settings = QSettings("MyCompany", "SqlmapGui")
        self.widgets_map = {}
        self.sqlmap_path = ""
        self.running_processes = {}
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.create_menu_bar()

        self.option_tabs = QTabWidget()
        self.add_option_tabs()
        main_layout.addWidget(self.option_tabs)

        self.output_tabs = QTabWidget()
        self.output_tabs.setTabsClosable(True)
        self.output_tabs.tabCloseRequested.connect(self.close_output_tab)
        self.output_tabs.currentChanged.connect(self.update_stop_button_state)
        main_layout.addWidget(self.output_tabs)

        self.run_external_checkbox = QCheckBox("Kör i externt fönster (ingen output/kontroll i GUI)")
        self.run_external_checkbox.setToolTip("Startar sqlmap i ett nytt terminalfönster...")
        main_layout.addWidget(self.run_external_checkbox)

        button_layout = QHBoxLayout()
        self.start_button = QPushButton("Starta Ny Skanning")
        self.start_button.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px; font-weight: bold;")
        self.start_button.clicked.connect(self.start_sqlmap_instance)
        button_layout.addWidget(self.start_button)

        self.copy_command_button = QPushButton("Kopiera Kommando")
        self.copy_command_button.setStyleSheet("padding: 8px;")
        self.copy_command_button.clicked.connect(self.copy_sqlmap_command_to_clipboard)
        button_layout.addWidget(self.copy_command_button)

        self.stop_button = QPushButton("Stoppa Aktiv Skanning")
        self.stop_button.setStyleSheet("background-color: #f44336; color: white; padding: 8px; font-weight: bold;")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_current_sqlmap_instance)
        button_layout.addWidget(self.stop_button)
        main_layout.addLayout(button_layout)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Redo.")

    def create_menu_bar(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&Arkiv")

        save_action = QAction("&Spara Inställningar", self)
        save_action.triggered.connect(self.save_settings)
        file_menu.addAction(save_action)

        load_action = QAction("&Ladda Inställningar", self)
        load_action.triggered.connect(self.load_settings)
        file_menu.addAction(load_action)
        
        reset_options_action = QAction("Återställ alla alternativ", self)
        reset_options_action.setStatusTip("Återställer alla alternativ i flikarna till deras standardvärden.")
        reset_options_action.triggered.connect(self.reset_all_options)
        file_menu.addAction(reset_options_action)

        sqlmap_path_action = QAction("Välj &sqlmap Sökväg...", self)
        sqlmap_path_action.triggered.connect(self.select_sqlmap_path)
        file_menu.addAction(sqlmap_path_action)
        file_menu.addSeparator()
        exit_action = QAction("&Avsluta", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menu_bar.addMenu("&Visa")
        theme_menu = view_menu.addMenu("Tema")
        
        self.theme_actions = []
        # Add system default option
        default_theme_action = QAction("Systemstandard", self, checkable=True)
        default_theme_action.triggered.connect(lambda: self.apply_theme(None)) # Pass None for default
        theme_menu.addAction(default_theme_action)
        self.theme_actions.append({"name": None, "action": default_theme_action})

        for style_name in QStyleFactory.keys():
            action = QAction(style_name, self, checkable=True)
            action.triggered.connect(lambda checked=False, name=style_name: self.apply_theme(name))
            theme_menu.addAction(action)
            self.theme_actions.append({"name": style_name, "action": action})

        help_menu = menu_bar.addMenu("&Hjälp")
        show_help_action = QAction("Visa sqlmap -hh", self)
        show_help_action.triggered.connect(self.show_sqlmap_help)
        help_menu.addAction(show_help_action)

    def add_option_tabs(self):
        while self.option_tabs.count():
            self.option_tabs.removeTab(0)
        self.widgets_map.clear()
        self.option_tabs.addTab(self.create_option_tab_content("Target"), "Target")
        self.option_tabs.addTab(self.create_option_tab_content("Request"), "Request")
        self.option_tabs.addTab(self.create_option_tab_content("Optimization"), "Optimization")
        self.option_tabs.addTab(self.create_option_tab_content("Injection"), "Injection")
        self.option_tabs.addTab(self.create_option_tab_content("Detection"), "Detection")
        self.option_tabs.addTab(self.create_option_tab_content("Techniques"), "Techniques")
        self.option_tabs.addTab(self.create_option_tab_content("Fingerprint"), "Fingerprint")
        self.option_tabs.addTab(self.create_option_tab_content("Enumeration"), "Enumeration")
        self.option_tabs.addTab(self.create_option_tab_content("Brute force"), "Brute force")
        self.option_tabs.addTab(self.create_option_tab_content("User-defined function injection"), "UDF Injection")
        self.option_tabs.addTab(self.create_option_tab_content("File system access"), "File System")
        self.option_tabs.addTab(self.create_option_tab_content("Operating system access"), "OS Access")
        self.option_tabs.addTab(self.create_option_tab_content("Windows registry access"), "Registry")
        self.option_tabs.addTab(self.create_option_tab_content("General"), "General")
        self.option_tabs.addTab(self.create_option_tab_content("Miscellaneous"), "Miscellaneous")

    def create_option_tab_content(self, category_name):
        tab_widget = QWidget()
        tab_layout = QVBoxLayout(tab_widget)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        form_layout = QFormLayout(scroll_content)
        scroll_area.setWidget(scroll_content)
        tab_layout.addWidget(scroll_area)

        # --- Add widgets based on category (content from previous version) ---
        if category_name == "Target":
            self.add_widget_option(form_layout, "Target URL", "target_url", "-u", "lineedit", tooltip="Mål-URL (t.ex. \"http://www.site.com/vuln.php?id=1\")")
            self.add_widget_option(form_layout, "Direct DB Connection", "target_direct", "-d", "lineedit", tooltip="Anslutningssträng för direkt databasanslutning")
            self.add_widget_option(form_layout, "Parse from Log File", "target_log", "-l", "file", tooltip="Parsa mål från Burp/WebScarab proxy loggfil")
            self.add_widget_option(form_layout, "Scan multiple targets file", "target_bulk", "-m", "file", tooltip="Skanna flera mål från en textfil")
            self.add_widget_option(form_layout, "Load HTTP request from file", "target_requestfile", "-r", "file", tooltip="Läs HTTP-förfrågan från fil")
            self.add_widget_option(form_layout, "Process Google dork", "target_google", "-g", "lineedit", tooltip="Bearbeta Google dork-resultat som mål-URL:er")
            self.add_widget_option(form_layout, "Load config file", "target_config", "-c", "file", tooltip="Läs alternativ från en INI-konfigurationsfil")

        elif category_name == "Request":
            self.add_widget_option(form_layout, "User-Agent (-A)", "req_agent", "-A", "lineedit")
            self.add_widget_option(form_layout, "Extra Header (-H)", "req_header", "-H", "lineedit", tooltip="Extra header (t.ex. \"X-Forwarded-For: 127.0.0.1\")")
            self.add_widget_option(form_layout, "HTTP Method (--method)", "req_method", "--method", "combo", items=["", "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"], tooltip="Tvinga användning av angiven HTTP-metod")
            self.add_widget_option(form_layout, "POST Data (--data)", "req_data", "--data", "lineedit", tooltip="Datasträng att skicka via POST (t.ex. \"id=1&user=test\")")
            self.add_widget_option(form_layout, "Param Delimiter (--param-del)", "req_param_del", "--param-del", "lineedit", tooltip="Tecken för att splitta parametervärden (t.ex. &)")
            self.add_widget_option(form_layout, "Cookie (--cookie)", "req_cookie", "--cookie", "lineedit", tooltip="HTTP Cookie header-värde (t.ex. \"PHPSESSID=a8d127e...\")")
            self.add_widget_option(form_layout, "Cookie Delimiter (--cookie-del)", "req_cookie_del", "--cookie-del", "lineedit", tooltip="Tecken för att splitta cookie-värden (t.ex. ;)")
            self.add_widget_option(form_layout, "Live Cookies File (--live-cookies)", "req_live_cookies", "--live-cookies", "file", tooltip="Fil med live-cookies för att ladda aktuella värden")
            self.add_widget_option(form_layout, "Load Cookies File (--load-cookies)", "req_load_cookies", "--load-cookies", "file", tooltip="Fil med cookies i Netscape/wget-format")
            self.add_widget_option(form_layout, "Ignore Set-Cookie (--drop-set-cookie)", "req_drop_cookie", "--drop-set-cookie", "checkbox")
            self.add_widget_option(form_layout, "Use HTTP/2 (--http2)", "req_http2", "--http2", "checkbox", tooltip="Använd HTTP version 2 (experimentellt)")
            self.add_widget_option(form_layout, "Imitate Mobile (--mobile)", "req_mobile", "--mobile", "checkbox", tooltip="Imitera smartphone via User-Agent header")
            self.add_widget_option(form_layout, "Random User-Agent (--random-agent)", "req_random_agent", "--random-agent", "checkbox")
            self.add_widget_option(form_layout, "Host Header (--host)", "req_host", "--host", "lineedit")
            self.add_widget_option(form_layout, "Referer Header (--referer)", "req_referer", "--referer", "lineedit")
            self.add_widget_option(form_layout, "Extra Headers (--headers)", "req_headers", "--headers", "textarea", tooltip="Extra headers, en per rad (t.ex. \"Accept-Language: fr\\nETag: 123\")")
            self.add_widget_option(form_layout, "Auth Type (--auth-type)", "req_auth_type", "--auth-type", "combo", items=["", "Basic", "Digest", "Bearer", "NTLM"], tooltip="HTTP autentiseringstyp")
            self.add_widget_option(form_layout, "Auth Credentials (--auth-cred)", "req_auth_cred", "--auth-cred", "lineedit", tooltip="HTTP autentiseringsuppgifter (format: name:password)")
            self.add_widget_option(form_layout, "Auth Cert/Key File (--auth-file)", "req_auth_file", "--auth-file", "file", tooltip="PEM certifikat/privat nyckel-fil för HTTP-autentisering")
            self.add_widget_option(form_layout, "Abort on HTTP Codes (--abort-code)", "req_abort_code", "--abort-code", "lineedit", tooltip="Avbryt vid (problematiska) HTTP-felkoder (t.ex. 401,403)")
            self.add_widget_option(form_layout, "Ignore HTTP Codes (--ignore-code)", "req_ignore_code", "--ignore-code", "lineedit", tooltip="Ignorera (problematiska) HTTP-felkoder (t.ex. 401)")
            self.add_widget_option(form_layout, "Ignore System Proxy (--ignore-proxy)", "req_ignore_proxy", "--ignore-proxy", "checkbox")
            self.add_widget_option(form_layout, "Ignore Redirects (--ignore-redirects)", "req_ignore_redirects", "--ignore-redirects", "checkbox")
            self.add_widget_option(form_layout, "Ignore Timeouts (--ignore-timeouts)", "req_ignore_timeouts", "--ignore-timeouts", "checkbox")
            self.add_widget_option(form_layout, "Proxy (--proxy)", "req_proxy", "--proxy", "lineedit", tooltip="Använd proxy (t.ex. http://127.0.0.1:8080, socks5://user:pass@host:port)")
            self.add_widget_option(form_layout, "Proxy Credentials (--proxy-cred)", "req_proxy_cred", "--proxy-cred", "lineedit", tooltip="Proxy autentiseringsuppgifter (format: name:password)")
            self.add_widget_option(form_layout, "Proxy List File (--proxy-file)", "req_proxy_file", "--proxy-file", "file", tooltip="Läs proxylista från fil")
            self.add_widget_option(form_layout, "Proxy Change Frequency (--proxy-freq)", "req_proxy_freq", "--proxy-freq", "spin", range=(1, 1000), default=1, tooltip="Antal förfrågningar mellan proxybyten från lista")
            self.add_widget_option(form_layout, "Use Tor (--tor)", "req_tor", "--tor", "checkbox")
            self.add_widget_option(form_layout, "Tor Port (--tor-port)", "req_tor_port", "--tor-port", "spin", range=(1, 65535), default=9050, tooltip="Ange Tor proxy-port (standard varierar)")
            self.add_widget_option(form_layout, "Tor Type (--tor-type)", "req_tor_type", "--tor-type", "combo", items=["SOCKS5", "SOCKS4", "HTTP"], default_index=0, tooltip="Ange Tor proxy-typ")
            self.add_widget_option(form_layout, "Check Tor Usage (--check-tor)", "req_check_tor", "--check-tor", "checkbox", tooltip="Kontrollera om Tor används korrekt")
            self.add_widget_option(form_layout, "Delay (sec) (--delay)", "req_delay", "--delay", "spin", range=(0, 300), default=0, tooltip="Fördröjning i sekunder mellan varje HTTP-förfrågan")
            self.add_widget_option(form_layout, "Timeout (sec) (--timeout)", "req_timeout", "--timeout", "spin", range=(1, 600), default=30, tooltip="Sekunder att vänta innan timeout (standard 30)")
            self.add_widget_option(form_layout, "Retries (--retries)", "req_retries", "--retries", "spin", range=(0, 10), default=3, tooltip="Antal försök vid timeout (standard 3)")
            self.add_widget_option(form_layout, "Retry on Regexp (--retry-on)", "req_retry_on", "--retry-on", "lineedit", tooltip="Försök igen om svaret matchar regexp (t.ex. \"drop\")")
            self.add_widget_option(form_layout, "Randomize Parameter (--randomize)", "req_randomize", "--randomize", "lineedit", tooltip="Slumpa värdet för angiven parameter")
            self.add_widget_option(form_layout, "Safe URL (--safe-url)", "req_safe_url", "--safe-url", "lineedit", tooltip="URL att besöka ofta under testning")
            self.add_widget_option(form_layout, "Safe POST Data (--safe-post)", "req_safe_post", "--safe-post", "lineedit", tooltip="POST-data att skicka till säker URL")
            self.add_widget_option(form_layout, "Safe Request File (--safe-req)", "req_safe_req", "--safe-req", "file", tooltip="Läs säker HTTP-förfrågan från fil")
            self.add_widget_option(form_layout, "Safe Request Frequency (--safe-freq)", "req_safe_freq", "--safe-freq", "spin", range=(1, 100), default=1, tooltip="Antal vanliga förfrågningar mellan besök till säker URL")
            self.add_widget_option(form_layout, "Skip URL Encoding (--skip-urlencode)", "req_skip_urlencode", "--skip-urlencode", "checkbox")
            self.add_widget_option(form_layout, "CSRF Token Parameter (--csrf-token)", "req_csrf_token", "--csrf-token", "lineedit", tooltip="Parameter som håller anti-CSRF-token")
            self.add_widget_option(form_layout, "CSRF Token URL (--csrf-url)", "req_csrf_url", "--csrf-url", "lineedit", tooltip="URL att besöka för att extrahera anti-CSRF-token")
            self.add_widget_option(form_layout, "CSRF Method (--csrf-method)", "req_csrf_method", "--csrf-method", "combo", items=["", "GET", "POST"], tooltip="HTTP-metod för CSRF-token-URL")
            self.add_widget_option(form_layout, "CSRF Data (--csrf-data)", "req_csrf_data", "--csrf-data", "lineedit", tooltip="POST-data att skicka till CSRF-token-URL")
            self.add_widget_option(form_layout, "CSRF Retries (--csrf-retries)", "req_csrf_retries", "--csrf-retries", "spin", range=(0, 10), default=0, tooltip="Antal försök att hämta CSRF-token (standard 0)")
            self.add_widget_option(form_layout, "Force SSL/HTTPS (--force-ssl)", "req_force_ssl", "--force-ssl", "checkbox")
            self.add_widget_option(form_layout, "Chunked Requests (--chunked)", "req_chunked", "--chunked", "checkbox", tooltip="Använd HTTP chunked transfer encoding (POST)")
            self.add_widget_option(form_layout, "HTTP Parameter Pollution (--hpp)", "req_hpp", "--hpp", "checkbox")
            self.add_widget_option(form_layout, "Evaluate Python Code (--eval)", "req_eval", "--eval", "textarea", tooltip="Utvärdera Python-kod före förfrågan (t.ex. \"import hashlib;id2=hashlib.md5(id).hexdigest()\")")

        elif category_name == "Optimization":
            self.add_widget_option(form_layout, "Turn on all optimizations (-o)", "opt_o", "-o", "checkbox")
            self.add_widget_option(form_layout, "Predict output (--predict-output)", "opt_predict", "--predict-output", "checkbox", tooltip="Förutsäg output från vanliga queries")
            self.add_widget_option(form_layout, "Keep-Alive (--keep-alive)", "opt_keep_alive", "--keep-alive", "checkbox", tooltip="Använd persistenta HTTP(s)-anslutningar")
            self.add_widget_option(form_layout, "Null connection (--null-connection)", "opt_null_conn", "--null-connection", "checkbox", tooltip="Hämta sidlängd utan att hämta HTTP response body")
            self.add_widget_option(form_layout, "Threads (--threads)", "opt_threads", "--threads", "spin", range=(1, 100), default=1, tooltip="Max antal samtidiga HTTP(s)-förfrågningar (standard 1)")

        elif category_name == "Injection":
            self.add_widget_option(form_layout, "Test Parameter (-p)", "inj_p", "-p", "lineedit", tooltip="Testbar(a) parameter(ar)")
            self.add_widget_option(form_layout, "Skip Parameter (--skip)", "inj_skip", "--skip", "lineedit", tooltip="Hoppa över testning av angiven(a) parameter(ar)")
            self.add_widget_option(form_layout, "Skip Static Params (--skip-static)", "inj_skip_static", "--skip-static", "checkbox", tooltip="Hoppa över testning av parametrar som inte verkar dynamiska")
            self.add_widget_option(form_layout, "Exclude Params (Regexp) (--param-exclude)", "inj_param_exclude", "--param-exclude", "lineedit", tooltip="Regexp för att exkludera parametrar från testning (t.ex. \"ses\")")
            self.add_widget_option(form_layout, "Filter Params by Place (--param-filter)", "inj_param_filter", "--param-filter", "lineedit", tooltip="Välj testbara parametrar efter plats (t.ex. GET, POST, URI, HEADER, COOKIE)")
            self.add_widget_option(form_layout, "Force DBMS (--dbms)", "inj_dbms", "--dbms", "lineedit", tooltip="Tvinga backend DBMS till angivet värde (t.ex. MySQL, PostgreSQL)")
            self.add_widget_option(form_layout, "DBMS Credentials (--dbms-cred)", "inj_dbms_cred", "--dbms-cred", "lineedit", tooltip="DBMS autentiseringsuppgifter (format: user:password)")
            self.add_widget_option(form_layout, "Force OS (--os)", "inj_os", "--os", "combo", items=["", "Linux", "Windows"], tooltip="Tvinga backend OS till angivet värde")
            self.add_widget_option(form_layout, "Invalidate with Big Numbers (--invalid-bignum)", "inj_invalid_bignum", "--invalid-bignum", "checkbox")
            self.add_widget_option(form_layout, "Invalidate with Logical Ops (--invalid-logical)", "inj_invalid_logical", "--invalid-logical", "checkbox")
            self.add_widget_option(form_layout, "Invalidate with Strings (--invalid-string)", "inj_invalid_string", "--invalid-string", "checkbox")
            self.add_widget_option(form_layout, "No Casting (--no-cast)", "inj_no_cast", "--no-cast", "checkbox", tooltip="Stäng av payload casting-mekanism")
            self.add_widget_option(form_layout, "No Escaping (--no-escape)", "inj_no_escape", "--no-escape", "checkbox", tooltip="Stäng av sträng-escaping-mekanism")
            self.add_widget_option(form_layout, "Payload Prefix (--prefix)", "inj_prefix", "--prefix", "lineedit", tooltip="Prefixsträng för injektionspayload")
            self.add_widget_option(form_layout, "Payload Suffix (--suffix)", "inj_suffix", "--suffix", "lineedit", tooltip="Suffixsträng för injektionspayload")
            self.add_widget_option(form_layout, "Tamper Script(s) (--tamper)", "inj_tamper", "--tamper", "lineedit", tooltip="Använd angivet skript för att manipulera injektionsdata (kommaseparerad lista eller enskilt skript)")

        elif category_name == "Detection":
            self.add_widget_option(form_layout, "Level (1-5) (--level)", "det_level", "--level", "spin", range=(1, 5), default=1, tooltip="Nivå av tester att utföra (1-5, standard 1)")
            self.add_widget_option(form_layout, "Risk (1-3) (--risk)", "det_risk", "--risk", "spin", range=(1, 3), default=1, tooltip="Risk för tester att utföra (1-3, standard 1)")
            self.add_widget_option(form_layout, "True String (--string)", "det_string", "--string", "lineedit", tooltip="Sträng att matcha när query utvärderas till True")
            self.add_widget_option(form_layout, "False String (--not-string)", "det_not_string", "--not-string", "lineedit", tooltip="Sträng att matcha när query utvärderas till False")
            self.add_widget_option(form_layout, "True Regexp (--regexp)", "det_regexp", "--regexp", "lineedit", tooltip="Regexp att matcha när query utvärderas till True")
            self.add_widget_option(form_layout, "True HTTP Code (--code)", "det_code", "--code", "spin", range=(100, 599), default=200, tooltip="HTTP-kod att matcha när query utvärderas till True (standard 200)")
            self.add_widget_option(form_layout, "Smart (--smart)", "det_smart", "--smart", "checkbox", tooltip="Utför grundliga tester endast vid positiv heuristik")
            self.add_widget_option(form_layout, "Text Only (--text-only)", "det_text_only", "--text-only", "checkbox", tooltip="Jämför sidor baserat endast på textinnehåll")
            self.add_widget_option(form_layout, "Titles Only (--titles)", "det_titles", "--titles", "checkbox", tooltip="Jämför sidor baserat endast på deras titlar")

        elif category_name == "Techniques":
             tech_group = QGroupBox("Techniques (--technique)")
             tech_layout = QHBoxLayout()
             self.add_widget_option(tech_layout, "B", "tech_B", "B", "checkbox", default=True)
             self.add_widget_option(tech_layout, "E", "tech_E", "E", "checkbox", default=True)
             self.add_widget_option(tech_layout, "U", "tech_U", "U", "checkbox", default=True)
             self.add_widget_option(tech_layout, "S", "tech_S", "S", "checkbox", default=True)
             self.add_widget_option(tech_layout, "T", "tech_T", "T", "checkbox", default=True)
             self.add_widget_option(tech_layout, "Q", "tech_Q", "Q", "checkbox", default=True)
             tech_group.setLayout(tech_layout)
             form_layout.addRow(tech_group)

             self.add_widget_option(form_layout, "Time Delay Secs (--time-sec)", "tech_time_sec", "--time-sec", "spin", range=(1, 300), default=5, tooltip="Sekunder att fördröja DBMS-svar (standard 5)")
             self.add_widget_option(form_layout, "UNION Columns Range (--union-cols)", "tech_union_cols", "--union-cols", "lineedit", tooltip="Intervall av kolumner att testa för UNION query (t.ex. 1-10)")
             self.add_widget_option(form_layout, "UNION Char (--union-char)", "tech_union_char", "--union-char", "lineedit", tooltip="Tecken att använda för bruteforce av antal kolumner (t.ex. NULL, 1)")
             self.add_widget_option(form_layout, "UNION FROM Table (--union-from)", "tech_union_from", "--union-from", "lineedit", tooltip="Tabell att använda i FROM-delen av UNION query")
             self.add_widget_option(form_layout, "UNION Values (--union-values)", "tech_union_values", "--union-values", "lineedit", tooltip="Kolumnvärden att använda för UNION query")
             self.add_widget_option(form_layout, "DNS Domain (--dns-domain)", "tech_dns_domain", "--dns-domain", "lineedit", tooltip="Domännamn för DNS exfiltration attack")
             self.add_widget_option(form_layout, "Second Order URL (--second-url)", "tech_second_url", "--second-url", "lineedit", tooltip="Resultatsidans URL att söka efter second-order svar")
             self.add_widget_option(form_layout, "Second Order Request File (--second-req)", "tech_second_req", "--second-req", "file", tooltip="Läs second-order HTTP-förfrågan från fil")

        elif category_name == "Fingerprint":
             self.add_widget_option(form_layout, "Extensive Fingerprint (-f, --fingerprint)", "fp_f", "--fingerprint", "checkbox", tooltip="Utför en omfattande DBMS versions-fingerprint")

        elif category_name == "Enumeration":
            group_box_enum_flags = QGroupBox("Flags (Vad ska hämtas?)")
            group_layout_enum_flags = QVBoxLayout(group_box_enum_flags)
            self.add_widget_option(group_layout_enum_flags, "All (-a)", "enum_all", "--all", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Banner (-b)", "enum_banner", "--banner", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Current User", "enum_current_user", "--current-user", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Current DB", "enum_current_db", "--current-db", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Hostname", "enum_hostname", "--hostname", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Is DBA", "enum_is_dba", "--is-dba", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Users", "enum_users", "--users", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Passwords", "enum_passwords", "--passwords", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Privileges", "enum_privileges", "--privileges", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Roles", "enum_roles", "--roles", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Databases", "enum_dbs", "--dbs", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Tables", "enum_tables", "--tables", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Columns", "enum_columns", "--columns", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Schema", "enum_schema", "--schema", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Count", "enum_count", "--count", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Dump", "enum_dump", "--dump", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Dump All", "enum_dump_all", "--dump-all", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "Search", "enum_search", "--search", "checkbox", tooltip="Sök kolumn(er), tabell(er), databasnamn (kräver -D, -T eller -C)")
            self.add_widget_option(group_layout_enum_flags, "Comments", "enum_comments", "--comments", "checkbox", tooltip="Leta efter DBMS-kommentarer under enumerering")
            self.add_widget_option(group_layout_enum_flags, "Statements", "enum_statements", "--statements", "checkbox", tooltip="Hämta SQL-satser som körs på DBMS")
            self.add_widget_option(group_layout_enum_flags, "Exclude SysDBs", "enum_exclude_sysdbs", "--exclude-sysdbs", "checkbox")
            self.add_widget_option(group_layout_enum_flags, "SQL Shell", "enum_sql_shell", "--sql-shell", "checkbox", tooltip="Starta interaktiv SQL-shell (fungerar dåligt här)")
            form_layout.addRow(group_box_enum_flags)

            group_box_enum_args = QGroupBox("Arguments (Var ska det hämtas?)")
            group_layout_enum_args = QFormLayout(group_box_enum_args)
            self.add_widget_option(group_layout_enum_args, "Database (-D)", "enum_D", "-D", "lineedit")
            self.add_widget_option(group_layout_enum_args, "Table(s) (-T)", "enum_T", "-T", "lineedit")
            self.add_widget_option(group_layout_enum_args, "Column(s) (-C)", "enum_C", "-C", "lineedit")
            self.add_widget_option(group_layout_enum_args, "User (-U)", "enum_U", "-U", "lineedit")
            self.add_widget_option(group_layout_enum_args, "Exclude Identifiers (-X)", "enum_X", "-X", "lineedit", tooltip="DBMS-identifierare att *inte* enumerera")
            self.add_widget_option(group_layout_enum_args, "Pivot Column (--pivot-column)", "enum_pivot_col", "--pivot-column", "lineedit")
            self.add_widget_option(group_layout_enum_args, "WHERE Condition (--where)", "enum_where", "--where", "lineedit", tooltip="Använd WHERE-villkor vid dumpning")
            self.add_widget_option(group_layout_enum_args, "First Entry (--start)", "enum_start", "--start", "spin", range=(0, 999999), default=0)
            self.add_widget_option(group_layout_enum_args, "Last Entry (--stop)", "enum_stop", "--stop", "spin", range=(0, 999999), default=0)
            self.add_widget_option(group_layout_enum_args, "First Char (--first)", "enum_first", "--first", "spin", range=(0, 999), default=0)
            self.add_widget_option(group_layout_enum_args, "Last Char (--last)", "enum_last", "--last", "spin", range=(0, 999), default=0)
            self.add_widget_option(group_layout_enum_args, "SQL Query (--sql-query)", "enum_sql_query", "--sql-query", "textarea", tooltip="SQL-sats att exekvera")
            self.add_widget_option(group_layout_enum_args, "SQL File (--sql-file)", "enum_sql_file", "--sql-file", "file", tooltip="Exekvera SQL-satser från fil")
            form_layout.addRow(group_box_enum_args)

        elif category_name == "Brute force":
             self.add_widget_option(form_layout, "Common Tables (--common-tables)", "brute_tables", "--common-tables", "checkbox")
             self.add_widget_option(form_layout, "Common Columns (--common-columns)", "brute_columns", "--common-columns", "checkbox")
             self.add_widget_option(form_layout, "Common Files (--common-files)", "brute_files", "--common-files", "checkbox")

        elif category_name == "User-defined function injection":
             self.add_widget_option(form_layout, "Inject UDF (--udf-inject)", "udf_inject", "--udf-inject", "checkbox")
             self.add_widget_option(form_layout, "Shared Library Path (--shared-lib)", "udf_shlib", "--shared-lib", "file", tooltip="Lokal sökväg till delat bibliotek (.so/.dll)")

        elif category_name == "File system access":
             self.add_widget_option(form_layout, "Read File (--file-read)", "fs_read", "--file-read", "lineedit", tooltip="Fil att läsa från backend filsystem")
             self.add_widget_option(form_layout, "Write Local File (--file-write)", "fs_write", "--file-write", "file", tooltip="Lokal fil att skriva till backend")
             self.add_widget_option(form_layout, "Destination File Path (--file-dest)", "fs_dest", "--file-dest", "lineedit", tooltip="Absolut sökväg på backend att skriva till")

        elif category_name == "Operating system access":
             self.add_widget_option(form_layout, "Execute OS Command (--os-cmd)", "os_cmd", "--os-cmd", "lineedit")
             self.add_widget_option(form_layout, "OS Shell (--os-shell)", "os_shell", "--os-shell", "checkbox", tooltip="Starta interaktiv OS-shell (fungerar dåligt här)")
             self.add_widget_option(form_layout, "OS Pwn (--os-pwn)", "os_pwn", "--os-pwn", "checkbox", tooltip="Försök få OOB shell, Meterpreter eller VNC (kräver mer setup)")
             self.add_widget_option(form_layout, "OS SMB Relay (--os-smbrelay)", "os_smbrelay", "--os-smbrelay", "checkbox")
             self.add_widget_option(form_layout, "OS BOF (--os-bof)", "os_bof", "--os-bof", "checkbox", tooltip="Stored procedure buffer overflow exploit")
             self.add_widget_option(form_layout, "Privilege Escalation (--priv-esc)", "os_privesc", "--priv-esc", "checkbox")
             self.add_widget_option(form_layout, "Metasploit Path (--msf-path)", "os_msfpath", "--msf-path", "directory", tooltip="Lokal sökväg där Metasploit Framework är installerat")
             self.add_widget_option(form_layout, "Remote Temp Path (--tmp-path)", "os_tmppath", "--tmp-path", "lineedit", tooltip="Absolut sökväg till temporär katalog på fjärrsystemet")

        elif category_name == "Windows registry access":
             self.add_widget_option(form_layout, "Read Registry Key (--reg-read)", "reg_read", "--reg-read", "checkbox")
             self.add_widget_option(form_layout, "Add Registry Key (--reg-add)", "reg_add", "--reg-add", "checkbox")
             self.add_widget_option(form_layout, "Delete Registry Key (--reg-del)", "reg_del", "--reg-del", "checkbox")
             self.add_widget_option(form_layout, "Registry Key (--reg-key)", "reg_key", "--reg-key", "lineedit", tooltip="Windows registernyckel (t.ex. HKLM\\SOFTWARE\\sqlmap)")
             self.add_widget_option(form_layout, "Registry Value (--reg-value)", "reg_value", "--reg-value", "lineedit", tooltip="Windows registernyckelvärde (t.ex. test)")
             self.add_widget_option(form_layout, "Registry Data (--reg-data)", "reg_data", "--reg-data", "lineedit", tooltip="Windows registernyckelvärdesdata")
             self.add_widget_option(form_layout, "Registry Type (--reg-type)", "reg_type", "--reg-type", "combo", items=["REG_SZ", "REG_DWORD", "REG_BINARY", "REG_MULTI_SZ", "REG_EXPAND_SZ"], tooltip="Windows registernyckelvärdestyp")

        elif category_name == "General":
            self.add_widget_option(form_layout, "Load Session File (-s)", "gen_s", "-s", "file", tooltip="Läs session från sparad (.sqlite) fil")
            self.add_widget_option(form_layout, "Log Traffic File (-t)", "gen_t", "-t", "file", is_save=True, tooltip="Logga all HTTP-trafik till en textfil")
            self.add_widget_option(form_layout, "Abort on Empty (--abort-on-empty)", "gen_abort_empty", "--abort-on-empty", "checkbox", tooltip="Avbryt datahämtning vid tomma resultat")
            self.add_widget_option(form_layout, "Predefined Answers (--answers)", "gen_answers", "--answers", "lineedit", tooltip='Fördefinierade svar (t.ex. "quit=N,follow=N,keep=Y")')
            self.add_widget_option(form_layout, "Base64 Parameter(s) (--base64)", "gen_base64", "--base64", "lineedit", tooltip="Parameter(ar) som innehåller Base64-kodad data")
            self.add_widget_option(form_layout, "URL Safe Base64 (--base64-safe)", "gen_base64_safe", "--base64-safe", "checkbox", tooltip="Använd URL- och filnamnssäkert Base64-alfabet (RFC 4648)")
            self.add_widget_option(form_layout, "Batch Mode (--batch)", "gen_batch", "--batch", "checkbox", tooltip="Fråga aldrig användaren, använd standardbeteende (denna GUI-inställning styr --batch-flaggan)") # Updated tooltip
            self.add_widget_option(form_layout, "Binary Fields (--binary-fields)", "gen_binary_fields", "--binary-fields", "lineedit", tooltip="Resultatfält med binära värden (t.ex. \"digest\")")
            self.add_widget_option(form_layout, "Check Internet (--check-internet)", "gen_check_internet", "--check-internet", "checkbox", tooltip="Kontrollera internetanslutning före testning")
            self.add_widget_option(form_layout, "Cleanup (--cleanup)", "gen_cleanup", "--cleanup", "checkbox", tooltip="Städa upp DBMS från sqlmap-specifika UDF:er och tabeller")
            self.add_widget_option(form_layout, "Crawl Depth (--crawl)", "gen_crawl", "--crawl", "spin", range=(0, 10), default=0, tooltip="Crawla webbplatsen från mål-URL (0=av)")
            self.add_widget_option(form_layout, "Crawl Exclude Regexp (--crawl-exclude)", "gen_crawl_exclude", "--crawl-exclude", "lineedit", tooltip="Regexp för att exkludera sidor från crawling (t.ex. \"logout\")")
            self.add_widget_option(form_layout, "CSV Delimiter (--csv-del)", "gen_csv_del", "--csv-del", "lineedit", default_text=",", tooltip="Avgränsningstecken för CSV-output (standard ',')")
            self.add_widget_option(form_layout, "Charset (--charset)", "gen_charset", "--charset", "lineedit", tooltip="Teckenuppsättning för blind SQL injection (t.ex. \"0123456789abcdef\")")
            self.add_widget_option(form_layout, "Dump To File (--dump-file)", "gen_dump_file", "--dump-file", "file", is_save=True, tooltip="Spara dumpad data till anpassad fil")
            self.add_widget_option(form_layout, "Dump Format (--dump-format)", "gen_dump_format", "--dump-format", "combo", items=["CSV", "HTML", "SQLITE"], default_index=0, tooltip="Format för dumpad data")
            self.add_widget_option(form_layout, "Encoding (--encoding)", "gen_encoding", "--encoding", "lineedit", tooltip="Teckenkodning för datahämtning (t.ex. GBK, latin1)")
            self.add_widget_option(form_layout, "Show ETA (--eta)", "gen_eta", "--eta", "checkbox")
            self.add_widget_option(form_layout, "Flush Session (--flush-session)", "gen_flush_session", "--flush-session", "checkbox", tooltip="Töm sessionsfiler för nuvarande mål")
            self.add_widget_option(form_layout, "Test Forms (--forms)", "gen_forms", "--forms", "checkbox", tooltip="Parsa och testa formulär på mål-URL")
            self.add_widget_option(form_layout, "Fresh Queries (--fresh-queries)", "gen_fresh_queries", "--fresh-queries", "checkbox", tooltip="Ignorera query-resultat sparade i sessionsfil")
            self.add_widget_option(form_layout, "Google Page (--gpage)", "gen_gpage", "--gpage", "spin", range=(1, 100), default=1, tooltip="Använd Google dork-resultat från angiven sida")
            self.add_widget_option(form_layout, "HAR File (--har)", "gen_har", "--har", "file", is_save=True, tooltip="Logga all HTTP-trafik till en HAR-fil")
            self.add_widget_option(form_layout, "Hex Conversion (--hex)", "gen_hex", "--hex", "checkbox", tooltip="Använd hex-konvertering vid datahämtning")
            self.add_widget_option(form_layout, "Output Directory (--output-dir)", "gen_output_dir", "--output-dir", "directory", tooltip="Anpassad sökväg för output-katalog")
            self.add_widget_option(form_layout, "Parse Errors (--parse-errors)", "gen_parse_errors", "--parse-errors", "checkbox", tooltip="Parsa och visa DBMS-felmeddelanden från svar")
            self.add_widget_option(form_layout, "Preprocess Script (--preprocess)", "gen_preprocess", "--preprocess", "file", tooltip="Använd skript för förbehandling (request)")
            self.add_widget_option(form_layout, "Postprocess Script (--postprocess)", "gen_postprocess", "--postprocess", "file", tooltip="Använd skript för efterbehandling (response)")
            self.add_widget_option(form_layout, "Repair (--repair)", "gen_repair", "--repair", "checkbox", tooltip="Dumpa om poster med okänt tecken (?)")
            self.add_widget_option(form_layout, "Save Config (--save)", "gen_save", "--save", "file", is_save=True, tooltip="Spara alternativ till en INI-konfigurationsfil")
            self.add_widget_option(form_layout, "Scope Regexp (--scope)", "gen_scope", "--scope", "lineedit", tooltip="Regexp för att filtrera mål")
            self.add_widget_option(form_layout, "Skip Heuristics (--skip-heuristics)", "gen_skip_heuristics", "--skip-heuristics", "checkbox")
            self.add_widget_option(form_layout, "Skip WAF Detection (--skip-waf)", "gen_skip_waf", "--skip-waf", "checkbox")
            self.add_widget_option(form_layout, "Table Prefix (--table-prefix)", "gen_table_prefix", "--table-prefix", "lineedit", default_text="sqlmap", tooltip="Prefix för temporära tabeller")
            self.add_widget_option(form_layout, "Test Filter (--test-filter)", "gen_test_filter", "--test-filter", "lineedit", tooltip="Välj tester efter payload/titel (t.ex. ROW, BENCHMARK)")
            self.add_widget_option(form_layout, "Test Skip (--test-skip)", "gen_test_skip", "--test-skip", "lineedit", tooltip="Hoppa över tester efter payload/titel")
            self.add_widget_option(form_layout, "Time Limit (sec) (--time-limit)", "gen_time_limit", "--time-limit", "spin", range=(0, 86400), default=0, tooltip="Kör med tidsgräns i sekunder (0=ingen)")
            self.add_widget_option(form_layout, "Unsafe Naming (--unsafe-naming)", "gen_unsafe_naming", "--unsafe-naming", "checkbox", tooltip="Inaktivera escaping av DBMS-identifierare")
            self.add_widget_option(form_layout, "Web Root (--web-root)", "gen_web_root", "--web-root", "lineedit", tooltip="Webbserverns dokumentrot (t.ex. /var/www)")

        elif category_name == "Miscellaneous":
             self.add_widget_option(form_layout, "Mnemonics (-z)", "misc_z", "-z", "lineedit", tooltip="Använd korta mnemonics (t.ex. \"flu,bat,ban,tec=EU\")")
             self.add_widget_option(form_layout, "Alert Command (--alert)", "misc_alert", "--alert", "lineedit", tooltip="Kör OS-kommando när SQL injection hittas")
             self.add_widget_option(form_layout, "Beep (--beep)", "misc_beep", "--beep", "checkbox", tooltip="Pip vid fråga och/eller när sårbarhet hittas")
             self.add_widget_option(form_layout, "Check Dependencies (--dependencies)", "misc_deps", "--dependencies", "checkbox", tooltip="Kontrollera saknade (valfria) sqlmap-beroenden (körs separat)")
             self.add_widget_option(form_layout, "Disable Coloring (--disable-coloring)", "misc_no_color", "--disable-coloring", "checkbox")
             self.add_widget_option(form_layout, "Disable Hashing (--disable-hashing)", "misc_no_hash", "--disable-hashing", "checkbox", tooltip="Inaktivera hashanalys vid tabelldumpning")
             self.add_widget_option(form_layout, "List Tampers (--list-tampers)", "misc_list_tampers", "--list-tampers", "checkbox", tooltip="Visa lista över tillgängliga tamper-skript (körs separat)")
             self.add_widget_option(form_layout, "No Logging (--no-logging)", "misc_no_log", "--no-logging", "checkbox", tooltip="Inaktivera loggning till fil")
             self.add_widget_option(form_layout, "Offline Mode (--offline)", "misc_offline", "--offline", "checkbox", tooltip="Arbeta i offline-läge (använd endast sessionsdata)")
             self.add_widget_option(form_layout, "Purge (--purge)", "misc_purge", "--purge", "checkbox", tooltip="Ta bort allt innehåll från sqlmap data-katalog säkert")
             self.add_widget_option(form_layout, "Results File (--results-file)", "misc_results", "--results-file", "file", is_save=True, tooltip="Plats för CSV-resultatfil i flermålsläge")
             self.add_widget_option(form_layout, "Interactive Shell (--shell)", "misc_shell", "--shell", "checkbox", tooltip="Starta interaktiv sqlmap-shell (fungerar dåligt här)")
             self.add_widget_option(form_layout, "Temp Directory (--tmp-dir)", "misc_tmpdir", "--tmp-dir", "directory", tooltip="Lokal katalog för temporära filer")
             self.add_widget_option(form_layout, "Unstable Connection (--unstable)", "misc_unstable", "--unstable", "checkbox", tooltip="Justera alternativ för instabila anslutningar")
             self.add_widget_option(form_layout, "Update Sqlmap (--update)", "misc_update", "--update", "checkbox", tooltip="Uppdatera sqlmap (körs separat)")
             self.add_widget_option(form_layout, "Wizard (--wizard)", "misc_wizard", "--wizard", "checkbox", tooltip="Enkelt wizard-gränssnitt (fungerar dåligt här)")

        return tab_widget

    def add_widget_option(self, layout_or_widget, label_text, widget_id, flag, widget_type, **kwargs):
        widget = None
        row_widget = None 
        actual_label_text = label_text

        if widget_type == "lineedit":
            widget = QLineEdit()
            if 'default_text' in kwargs: widget.setText(kwargs['default_text'])
            row_widget = widget
        elif widget_type == "textarea":
             widget = QTextEdit()
             widget.setAcceptRichText(False); widget.setFixedHeight(60)
             if 'default_text' in kwargs: widget.setPlainText(kwargs['default_text'])
             row_widget = widget
        elif widget_type == "checkbox":
            widget = QCheckBox(label_text)
            if 'default' in kwargs: widget.setChecked(kwargs['default'])
            if isinstance(layout_or_widget, QFormLayout):
                 row_widget = widget
                 actual_label_text = "" 
            elif isinstance(layout_or_widget, QLayout):
                 layout_or_widget.addWidget(widget)
                 row_widget = None 
                 actual_label_text = ""
            elif isinstance(layout_or_widget, QWidget):
                 if layout_or_widget.layout() is not None:
                     layout_or_widget.layout().addWidget(widget)
                     row_widget = None 
                     actual_label_text = ""
                 else:
                      row_widget = widget
            else:
                 row_widget = widget
        elif widget_type == "combo":
            widget = QComboBox()
            widget.addItems(kwargs.get('items', []))
            default_index = kwargs.get('default_index', -1)
            if 0 <= default_index < widget.count(): widget.setCurrentIndex(default_index)
            row_widget = widget
        elif widget_type == "spin":
            widget = QSpinBox()
            widget.setRange(*kwargs.get('range', (0, 99999)))
            widget.setValue(kwargs.get('default', 0))
            if 'step' in kwargs: widget.setSingleStep(kwargs['step'])
            row_widget = widget
        elif widget_type == "file" or widget_type == "directory":
             is_dir = (widget_type == "directory")
             is_save = kwargs.get('is_save', False)
             container, widget = create_file_input(label_text, self, is_directory=is_dir)
             button = container.findChild(QPushButton)
             if button and is_save:
                 try: button.clicked.disconnect()
                 except RuntimeError: pass
                 button.clicked.connect(lambda checked=False, le=widget, save=True, isdir=is_dir: self.select_file_or_dir(le, save, isdir))
             row_widget = container
             actual_label_text = ""
        else:
            widget = QLabel(f"Unimplemented: {widget_type}")
            row_widget = widget

        if row_widget and isinstance(layout_or_widget, QFormLayout):
            layout_or_widget.addRow(actual_label_text, row_widget)
        elif row_widget is None and actual_label_text == "":
             pass 
        elif row_widget and isinstance(layout_or_widget, QLayout):
             if actual_label_text: layout_or_widget.addWidget(QLabel(actual_label_text))
             layout_or_widget.addWidget(row_widget)
        elif row_widget and isinstance(layout_or_widget, QWidget) and layout_or_widget.layout() is not None:
             if actual_label_text: layout_or_widget.layout().addWidget(QLabel(actual_label_text))
             layout_or_widget.layout().addWidget(row_widget)

        if widget:
            tooltip = kwargs.get('tooltip')
            if tooltip:
                (row_widget if row_widget and not isinstance(row_widget, QLayout) else widget).setToolTip(tooltip)

        if widget:
            self.widgets_map[widget_id] = {
                "widget": widget,
                "flag": flag,
                "type": widget_type,
                "default": kwargs.get('default', False if widget_type == 'checkbox' else (0 if widget_type == 'spin' else None)), # Adjusted default for None
                "default_text": kwargs.get('default_text', ""), 
                "default_index": kwargs.get('default_index', -1),
                "container": row_widget if row_widget is not widget and isinstance(row_widget, QWidget) else None
            }
        else:
            print(f"Warning: Widget for ID '{widget_id}' was not created.")

    @Slot(QLineEdit, bool, bool)
    def select_file_or_dir(self, line_edit_widget, is_save=False, is_directory=False):
        path = ""
        current_dir = os.path.dirname(line_edit_widget.text()) if line_edit_widget.text() and os.path.isdir(os.path.dirname(line_edit_widget.text())) else os.path.expanduser("~")
        if is_directory:
            path = QFileDialog.getExistingDirectory(self, "Välj katalog", current_dir)
        elif is_save:
            path, _ = QFileDialog.getSaveFileName(self, "Spara fil som...", current_dir)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Välj fil", current_dir)
        if path:
            line_edit_widget.setText(path)

    @Slot(QLineEdit)
    def select_file(self, line_edit_widget):
         self.select_file_or_dir(line_edit_widget, is_save=False, is_directory=False)

    @Slot(QLineEdit)
    def select_directory(self, line_edit_widget):
        self.select_file_or_dir(line_edit_widget, is_save=False, is_directory=True)

    def build_sqlmap_command(self):
        if not self.sqlmap_path:
             QMessageBox.warning(self, "Sqlmap Sökväg Saknas", "Sökvägen till sqlmap är inte inställd...")
             return None, False
        command_parts = shlex.split(self.sqlmap_path)
        executable = command_parts[0]
        if not os.path.exists(executable) and shutil.which(executable) is None:
             QMessageBox.warning(self, "Sqlmap Sökväg Ogiltig", f"Kommandot '{executable}'... hittades inte...")
             return None, False

        command_base = list(command_parts)
        if self.sqlmap_path.lower().endswith(".py"):
            is_python_call = any(command_base[0].lower().endswith(os.path.basename(py_exe).lower()) for py_exe in ["python", "python3", sys.executable])
            if not is_python_call and shutil.which(command_base[0]) is None:
                 command_base.insert(0, sys.executable)
        
        command = list(command_base)
        target_specified = False
        target_options = ["-u", "-d", "-l", "-m", "-r", "-g", "-c"]

        for widget_id, data in self.widgets_map.items():
            if widget_id.startswith("tech_") and data["type"] == "checkbox":
                 continue
            widget = data["widget"]
            flag = data["flag"]
            widget_type = data["type"]
            value = None
            is_default = True
            try:
                if widget_type == "lineedit":
                    value = widget.text().strip()
                    default_val = data.get('default_text', "")
                    is_default = (value == default_val)
                elif widget_type == "textarea":
                    value = widget.toPlainText().strip()
                    default_val = data.get('default_text', "")
                    is_default = (value == default_val)
                elif widget_type == "checkbox":
                    value = widget.isChecked()
                    is_default = (value == data.get('default', False))
                    if not is_default: value = True 
                elif widget_type == "combo":
                    value = widget.currentText()
                    default_index = data.get("default_index", -1)
                    is_default = (widget.currentIndex() == default_index) or (not value and default_index == -1)
                elif widget_type == "spin":
                    value = widget.value()
                    default_val = data.get("default", 0)
                    is_default = (value == default_val)
                    value = str(value)
                elif widget_type in ["file", "directory"]:
                    value = widget.text().strip()
                    is_default = (value == "")
            except Exception as e:
                print(f"Error reading widget {widget_id}: {e}")
                continue

            if not is_default and value is not None and (value != "" or isinstance(value, bool)): # Ensure bool True is added
                 if flag in target_options:
                    target_specified = True
                 if value is True: 
                     command.append(flag)
                 elif flag.startswith("--"):
                     command.append(f"{flag}={value}")
                 else:
                     command.extend([flag, value])
        
        tech_string = "".join([data["flag"] for cb_id, data in self.widgets_map.items()
                               if cb_id.startswith("tech_") and data["type"] == "checkbox" and data["widget"].isChecked()])
        default_tech = "BEUSTQ"
        if set(tech_string) != set(default_tech):
             if tech_string:
                 command.append(f"--technique={tech_string}")
        return command, target_specified

    @Slot()
    def copy_sqlmap_command_to_clipboard(self):
        command_list, _ = self.build_sqlmap_command()
        if command_list:
            # Use shlex.join for proper quoting if available (Python 3.8+)
            # Otherwise, a simpler join with manual quoting for basic cases.
            try:
                command_str = shlex.join(command_list)
            except AttributeError: # shlex.join not available
                command_str = ' '.join(shlex.quote(str(arg)) for arg in command_list)
            
            QGuiApplication.clipboard().setText(command_str)
            self.status_bar.showMessage("Kommando kopierat till urklipp!", 3000)
        else:
            self.status_bar.showMessage("Kunde inte bygga kommandot för kopiering.", 3000)

    @Slot()
    def reset_all_options(self):
        reply = QMessageBox.question(self, "Återställ alternativ", 
                                     "Är du säker på att du vill återställa alla alternativ till deras standardvärden?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            return

        for widget_id, data in self.widgets_map.items():
            widget = data["widget"]
            widget_type = data["type"]
            try:
                if widget_type == "lineedit":
                    widget.setText(data.get('default_text', ""))
                elif widget_type == "textarea":
                    widget.setPlainText(data.get('default_text', ""))
                elif widget_type == "checkbox":
                    widget.setChecked(data.get('default', False))
                elif widget_type == "combo":
                    default_idx = data.get('default_index', -1)
                    if 0 <= default_idx < widget.count():
                        widget.setCurrentIndex(default_idx)
                    elif widget.count() > 0 : # Fallback to first item if default_index is bad but items exist
                        widget.setCurrentIndex(0 if widget.itemText(0) == "" or default_idx == -1 else default_idx if default_idx < widget.count() else 0)

                elif widget_type == "spin":
                    widget.setValue(data.get('default', 0))
                elif widget_type in ["file", "directory"]: # QLineEdit inside a container
                     widget.setText("") # Default for file/dir paths is empty
            except Exception as e:
                print(f"Error resetting widget {widget_id}: {e}")
        
        self.run_external_checkbox.setChecked(False) # Default for external run
        self.status_bar.showMessage("Alla alternativ har återställts.", 3000)


    @Slot()
    def start_sqlmap_instance(self):
        command_list, target_specified = self.build_sqlmap_command()
        if command_list is None: return
        if not target_specified:
             if not any(str(opt) in ['-r', '-c'] for opt in command_list):
                 QMessageBox.warning(self, "Inget Mål", "Minst ett mål (-u, -l, -r, -g, -c, -d) måste anges.")
                 return

        # Warning for interactive flags if not external and no batch
        if not self.run_external_checkbox.isChecked():
            interactive_flags_used = ['--sql-shell', '--os-shell', '--os-pwn', '--wizard', '--shell']
            has_interactive = any(str(arg).split('=')[0] in interactive_flags_used for arg in command_list)
            has_batch = "--batch" in command_list or any(str(arg).startswith("--batch=") for arg in command_list)

            if has_interactive and not has_batch:
                reply = QMessageBox.warning(self, "Interaktivt Kommando",
                                            "Du har valt interaktiva flaggor (t.ex. --os-shell) utan --batch och utan att köra i externt fönster.\n"
                                            "Detta kanske inte fungerar som förväntat i GUI-fliken.\n\n"
                                            "Rekommendation:\n"
                                            "- Använd 'Kör i externt fönster' för interaktiva sessioner, ELLER\n"
                                            "- Lägg till '--batch' (via General-fliken) för icke-interaktiv körning.\n\n"
                                            "Vill du fortsätta ändå?",
                                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                            QMessageBox.StandardButton.Cancel)
                if reply == QMessageBox.StandardButton.Cancel:
                    return

        if self.run_external_checkbox.isChecked():
            self.start_sqlmap_externally(command_list)
        else:
            output_edit = QTextEdit()
            output_edit.setReadOnly(True)
            output_edit.setFont(QFont("monospace"))
            output_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            thread = SqlmapRunnerThread(command_list)
            instance_id = thread.identifier
            thread.outputTextAvailable.connect(self.update_output)
            thread.processFinished.connect(self.on_process_finished)
            thread.processError.connect(self.on_process_error)
            self.running_processes[instance_id] = {"thread": thread, "output_widget": output_edit}
            tab_title = f"Run {instance_id}"
            target_arg_display = ""
            for i, arg_val in enumerate(command_list):
                arg = str(arg_val)
                if arg in ["-u", "--url", "-r", "-l", "-g", "-d", "-m", "-c"] and i + 1 < len(command_list):
                    target_arg_display = str(command_list[i+1])
                    if len(target_arg_display) > 20: target_arg_display = "..." + target_arg_display[-17:]
                    tab_title = f"{instance_id}: {os.path.basename(target_arg_display)}"
                    break
                elif arg.startswith("--url="):
                     target_arg_display = arg.split("=",1)[1]
                     if len(target_arg_display) > 20: target_arg_display = "..." + target_arg_display[-17:]
                     tab_title = f"{instance_id}: {target_arg_display}"
                     break
            tab_index = self.output_tabs.addTab(output_edit, tab_title)
            self.output_tabs.setTabToolTip(tab_index, f"Kommando: {' '.join(map(shlex.quote, map(str,command_list)))}")
            self.output_tabs.setCurrentIndex(tab_index)
            thread.start()
            self.update_stop_button_state()
            self.status_bar.showMessage(f"Startade skanning {instance_id} i flik...")

    def start_sqlmap_externally(self, command_list):
        str_command_list = [str(item) for item in command_list]
        try:
            current_os = platform.system()
            if current_os == "Windows":
                full_command_str = subprocess.list2cmdline(str_command_list)
                final_cmd_str = f'title Sqlmap Output & {full_command_str} & echo. & echo Processen är klar. Resultatet visas ovan. & pause'
                subprocess.Popen(['cmd', '/C', 'start', 'cmd', '/K', final_cmd_str], shell=False, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
                self.status_bar.showMessage("SQLMap startad i externt CMD-fönster...", 5000)
            elif current_os == "Linux":
                full_command_str = ' '.join(shlex.quote(arg) for arg in str_command_list)
                prompt_text_linux = '\\n\\nProcessen är klar. Resultatet visas ovan.\\nTryck ENTER för att stänga detta fönster... '
                term_script_after_command = f"printf '{prompt_text_linux}'; read"
                if shutil.which('xterm'):
                    subprocess.Popen(['xterm', '-hold', '-e', 'sh', '-c', f"{full_command_str}; printf '\\nProcessen är klar. Fönstret hålls öppet av xterm (-hold).'"])
                    self.status_bar.showMessage("SQLMap startad i xterm (hålls öppen)...", 5000)
                    return
                terminals = [
                    ('gnome-terminal', ['gnome-terminal', '--', 'sh', '-c', f"{full_command_str}; {term_script_after_command}"]),
                    ('konsole', ['konsole', '-e', 'sh', '-c', f"{full_command_str}; {term_script_after_command}"]),
                ]
                escaped_full_cmd = full_command_str.replace('"', '\\"')
                escaped_after_cmd = term_script_after_command.replace('"', '\\"')
                terminals.extend([
                    ('xfce4-terminal', ['xfce4-terminal', '--disable-factory', '--command', f"sh -c \"{escaped_full_cmd}; {escaped_after_cmd}\""]),
                    ('lxterminal', ['lxterminal', '-e', f"sh -c \"{escaped_full_cmd}; {escaped_after_cmd}\""]),
                    ('mate-terminal', ['mate-terminal', '--disable-factory', '--command', f"sh -c \"{escaped_full_cmd}; {escaped_after_cmd}\""]),
                ])
                launched = False
                for term_name, term_cmd_parts in terminals:
                    if shutil.which(term_name):
                        subprocess.Popen(term_cmd_parts)
                        launched = True
                        self.status_bar.showMessage(f"SQLMap startad i {term_name}...", 5000)
                        break
                if not launched:
                    if shutil.which('x-terminal-emulator'):
                        subprocess.Popen(['x-terminal-emulator', '-e', 'sh', '-c', f"{full_command_str}; {term_script_after_command}"])
                        self.status_bar.showMessage("SQLMap startad i x-terminal-emulator...", 5000)
                    else:
                        QMessageBox.warning(self, "Fel", "Kunde inte hitta en lämplig terminalemulator...")
                        self.status_bar.showMessage("Kunde inte starta externt fönster.", 5000)
            elif current_os == "Darwin":
                full_command_str_for_shell = ' '.join(shlex.quote(arg) for arg in str_command_list)
                prompt_text_macos = 'Processen är klar. Resultatet visas ovan.\\\\nTryck RETUR för att stänga detta fönster... '
                shell_command_suffix_macos = f"printf '{prompt_text_macos}'; read"
                complete_shell_command = f"{full_command_str_for_shell}; {shell_command_suffix_macos}"
                applescript_do_script_payload = complete_shell_command.replace('\\', '\\\\').replace('"', '\\"')
                script_content = f'tell application "Terminal"\n  if not (exists window 1) then reopen\n  activate\n  do script "{applescript_do_script_payload}" in window 1\nend tell'
                subprocess.Popen(['osascript', '-e', script_content])
                self.status_bar.showMessage("SQLMap startad i externt Terminal.app fönster...", 5000)
            else:
                QMessageBox.warning(self, "Operativsystem ej stödd", f"Att starta i externt fönster stöds inte på {current_os}...")
                self.status_bar.showMessage("Externt fönster ej stödd på detta OS.", 5000)
        except FileNotFoundError as e:
            QMessageBox.critical(self, "Fel vid start av extern process", f"Kunde inte hitta kommandot: {e.filename}...")
            self.status_bar.showMessage("Fel vid start av extern process.", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Fel", f"Ett oväntat fel uppstod...: {e}")
            self.status_bar.showMessage("Fel vid start av externt fönster.", 5000)

    @Slot()
    def stop_current_sqlmap_instance(self):
        current_index = self.output_tabs.currentIndex()
        if current_index < 0: return
        current_widget = self.output_tabs.widget(current_index)
        instance_id_to_stop = None
        for id_val, data in self.running_processes.items():
            if data["output_widget"] == current_widget:
                instance_id_to_stop = id_val
                break
        if instance_id_to_stop is not None and instance_id_to_stop in self.running_processes:
            thread_to_stop = self.running_processes[instance_id_to_stop]["thread"]
            if thread_to_stop.isRunning():
                thread_to_stop.stop()
            else:
                 self.update_stop_button_state()
        else:
             self.update_stop_button_state()

    @Slot(int)
    def close_output_tab(self, index):
        widget_to_close = self.output_tabs.widget(index)
        instance_id_to_stop = None
        thread_to_stop = None
        for id_val, data in self.running_processes.items():
            if data["output_widget"] == widget_to_close:
                instance_id_to_stop = id_val
                thread_to_stop = data["thread"]
                break
        if thread_to_stop and thread_to_stop.isRunning():
            reply = QMessageBox.question(self, 'Stoppa och Stäng?', f'Sqlmap-processen för flik "{self.output_tabs.tabText(index)}" körs fortfarande...', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
            if reply == QMessageBox.StandardButton.Yes: thread_to_stop.stop()
            else: return
        self.output_tabs.removeTab(index)
        if instance_id_to_stop in self.running_processes and (thread_to_stop is None or not thread_to_stop.isRunning()):
             del self.running_processes[instance_id_to_stop]
        self.update_stop_button_state()

    @Slot(int, str)
    def update_output(self, instance_id, text):
        if instance_id in self.running_processes:
            output_widget = self.running_processes[instance_id]["output_widget"]
            output_widget.moveCursor(QTextCursor.MoveOperation.End)
            output_widget.insertPlainText(text)
            output_widget.moveCursor(QTextCursor.MoveOperation.End)

    @Slot(int, int)
    def on_process_finished(self, instance_id, return_code):
        if instance_id in self.running_processes:
            tab_index = -1
            output_widget = self.running_processes[instance_id]["output_widget"]
            for i in range(self.output_tabs.count()):
                if self.output_tabs.widget(i) == output_widget:
                    tab_index = i
                    break
            status_msg = f"Skanning {instance_id} avslutad (kod {return_code})."
            if tab_index != -1:
                 current_title = self.output_tabs.tabText(tab_index)
                 if "[Kör]" in current_title: current_title = current_title.replace("[Kör]", "").strip()
                 self.output_tabs.setTabText(tab_index, f"{current_title} [Klar]")
                 self.output_tabs.setTabToolTip(tab_index, self.output_tabs.tabToolTip(tab_index) + f"\nAvslutad med kod {return_code}")
            del self.running_processes[instance_id]
            self.status_bar.showMessage(status_msg, 5000)
            self.update_stop_button_state()

    @Slot(int, str)
    def on_process_error(self, instance_id, error_message):
        if instance_id in self.running_processes:
            output_widget = self.running_processes[instance_id]["output_widget"]
            output_widget.append(f"\n[FEL] {error_message}\n")
            tab_index = -1
            for i in range(self.output_tabs.count()):
                if self.output_tabs.widget(i) == output_widget:
                    tab_index = i
                    break
            status_msg = f"Fel vid körning av skanning {instance_id}."
            if tab_index != -1:
                 current_title = self.output_tabs.tabText(tab_index)
                 if "[Kör]" in current_title: current_title = current_title.replace("[Kör]", "").strip()
                 self.output_tabs.setTabText(tab_index, f"{current_title} [Fel]")
                 self.output_tabs.setTabToolTip(tab_index, self.output_tabs.tabToolTip(tab_index) + f"\nFel: {error_message}")
            if instance_id in self.running_processes: del self.running_processes[instance_id]
            self.status_bar.showMessage(status_msg, 5000)
            self.update_stop_button_state()

    @Slot()
    def update_stop_button_state(self):
        current_index = self.output_tabs.currentIndex()
        is_stoppable = False
        if current_index >= 0:
            current_widget = self.output_tabs.widget(current_index)
            for data in self.running_processes.values():
                if data["output_widget"] == current_widget and data["thread"].isRunning():
                    is_stoppable = True
                    break
        self.stop_button.setEnabled(is_stoppable)

    @Slot()
    def select_sqlmap_path(self):
        current_dir = os.path.dirname(self.sqlmap_path) if self.sqlmap_path and os.path.exists(os.path.dirname(self.sqlmap_path)) else os.path.expanduser("~")
        file_path, _ = QFileDialog.getOpenFileName(self, "Välj sqlmap körbar fil...", current_dir, "Program (sqlmap* sqlmap.py);;Python Scripts (*.py);;All Files (*)")
        if file_path:
            self.sqlmap_path = file_path
            self.settings.setValue("app/sqlmapPath", self.sqlmap_path)

    @Slot()
    def save_settings(self):
        self.settings.setValue("app/sqlmapPath", self.sqlmap_path)
        self.settings.setValue("app/runExternal", self.run_external_checkbox.isChecked())
        current_style = QApplication.style().objectName()
        selected_theme_name = "Systemstandard" # Default if no specific match
        for theme_info in self.theme_actions:
            if theme_info["action"].isChecked():
                selected_theme_name = theme_info["name"] if theme_info["name"] is not None else "Systemstandard"
                break
        self.settings.setValue("app/theme", selected_theme_name)

        self.settings.beginGroup("Widgets")
        for widget_id, data in self.widgets_map.items():
            widget = data["widget"]
            widget_type = data["type"]
            value = None
            try:
                if widget_type in ["lineedit", "textarea", "file", "directory"]: value = widget.text().strip() if isinstance(widget, QLineEdit) else widget.toPlainText().strip()
                elif widget_type == "checkbox": value = widget.isChecked()
                elif widget_type == "combo": value = widget.currentIndex()
                elif widget_type == "spin": value = widget.value()
                if value is not None: self.settings.setValue(widget_id, value)
            except Exception as e: print(f"Warning: Could not save setting for {widget_id}: {e}")
        self.settings.endGroup()
        self.status_bar.showMessage("Inställningar sparade.", 3000)

    @Slot()
    def load_settings(self):
        self.sqlmap_path = self.settings.value("app/sqlmapPath", "")
        self.run_external_checkbox.setChecked(self.settings.value("app/runExternal", False, type=bool))
        
        saved_theme = self.settings.value("app/theme", "Systemstandard")
        self.apply_theme(saved_theme if saved_theme != "Systemstandard" else None, from_load=True)


        if not self.sqlmap_path or not (os.path.exists(self.sqlmap_path) or shutil.which(shlex.split(self.sqlmap_path)[0] if self.sqlmap_path else "")):
            found_path = shutil.which("sqlmap") or shutil.which("sqlmap.py")
            if found_path: self.sqlmap_path = os.path.abspath(found_path)
            elif os.path.exists("./sqlmap.py"): self.sqlmap_path = os.path.abspath("./sqlmap.py")
            elif os.path.exists(os.path.join(os.path.dirname(sys.executable), "sqlmap.py")): self.sqlmap_path = os.path.join(os.path.dirname(sys.executable), "sqlmap.py")

        self.settings.beginGroup("Widgets")
        for widget_id, data in self.widgets_map.items():
             if self.settings.contains(widget_id):
                 saved_value = self.settings.value(widget_id)
                 widget = data["widget"]
                 widget_type = data["type"]
                 try:
                     if widget_type in ["lineedit", "textarea", "file", "directory"]: widget.setText(str(saved_value)) if isinstance(widget, QLineEdit) else widget.setPlainText(str(saved_value))
                     elif widget_type == "checkbox": widget.setChecked(str(saved_value).lower() in ['true', '1', 'yes'])
                     elif widget_type == "combo":
                         index = int(saved_value)
                         if 0 <= index < widget.count(): widget.setCurrentIndex(index)
                     elif widget_type == "spin": widget.setValue(int(saved_value))
                 except Exception as e: print(f"Warning: Couldn't load setting for {widget_id}: {e}")
        self.settings.endGroup()
        self.status_bar.showMessage("Inställningar laddade.", 3000)

    @Slot(str)
    def apply_theme(self, theme_name, from_load=False):
        current_app = QApplication.instance()
        if theme_name is None or theme_name == "Systemstandard": # System default
            # To truly revert to system default, we might need to restart or re-create app with no style set.
            # For now, setting Fusion as a good default or clearing the style.
            # If platform has a specific "system" style, that could be used.
            # Let's try setting a known good default like Fusion, or the original style.
            if hasattr(self, '_original_style_name'):
                 QApplication.setStyle(QStyleFactory.create(self._original_style_name))
            else: # Fallback if original not stored (e.g. first run)
                 QApplication.setStyle(QStyleFactory.create("Fusion")) # Fusion is generally available
            selected_theme_name_for_settings = "Systemstandard"
        elif theme_name in QStyleFactory.keys():
            QApplication.setStyle(QStyleFactory.create(theme_name))
            selected_theme_name_for_settings = theme_name
        else:
            if not from_load: # Don't show error if it's from loading a non-existent saved theme
                QMessageBox.warning(self, "Temafel", f"Kunde inte applicera temat '{theme_name}'. Stilen finns inte.")
            return

        # Update checkmarks in menu
        for theme_info in self.theme_actions:
            is_current_theme = (theme_info["name"] == theme_name) or \
                               (theme_name is None and theme_info["name"] is None) or \
                               (theme_name == "Systemstandard" and theme_info["name"] is None)
            theme_info["action"].setChecked(is_current_theme)
        
        if not from_load: # Avoid double-saving when called from load_settings
            self.settings.setValue("app/theme", selected_theme_name_for_settings)
            self.status_bar.showMessage(f"Tema ändrat till {selected_theme_name_for_settings}.", 2000)


    @Slot()
    def show_sqlmap_help(self):
        if not self.sqlmap_path or not (os.path.exists(self.sqlmap_path) or shutil.which(shlex.split(self.sqlmap_path)[0] if self.sqlmap_path else "")):
            QMessageBox.warning(self, "Sqlmap Sökväg Saknas/Ogiltig", "Ange en giltig sökväg till sqlmap...")
            return
        command_base_parts = shlex.split(self.sqlmap_path)
        if self.sqlmap_path.lower().endswith(".py"):
            is_python_call = any(command_base_parts[0].lower().endswith(os.path.basename(py_exe).lower()) for py_exe in ["python", "python3", sys.executable])
            if not is_python_call and shutil.which(command_base_parts[0]) is None:
                 command_base_parts.insert(0, sys.executable)
        command = command_base_parts + ["-hh"]
        try:
            self.status_bar.showMessage("Hämtar hjälptext...")
            QApplication.processEvents()
            result = subprocess.run(command, capture_output=True, text=True, timeout=15, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0)
            dialog = QDialog(self)
            dialog.setWindowTitle("sqlmap -hh Hjälp"); dialog.setGeometry(150, 150, 800, 600)
            layout = QVBoxLayout(dialog)
            help_text_edit = QTextEdit(); help_text_edit.setReadOnly(True); help_text_edit.setFont(QFont("monospace")); help_text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            if result.returncode == 0: help_text_edit.setText(result.stdout)
            else: help_text_edit.setText(f"Kunde inte köra '{' '.join(command)}'.\nFelkod: {result.returncode}\nOutput:\n{result.stdout}\nError:\n{result.stderr}")
            layout.addWidget(help_text_edit)
            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok); button_box.accepted.connect(dialog.accept); layout.addWidget(button_box)
            dialog.exec()
        except FileNotFoundError: QMessageBox.critical(self, "Fel", f"Kunde inte hitta '{command_base_parts[0]}'.")
        except subprocess.TimeoutExpired: QMessageBox.critical(self, "Fel", "Timeout när hjälptexten skulle hämtas.")
        except Exception as e: QMessageBox.critical(self, "Fel", f"Kunde inte hämta hjälptext: {e}")
        finally: self.status_bar.showMessage("Redo.")

    def closeEvent(self, event):
        running_threads = [data["thread"] for data in self.running_processes.values() if data["thread"].isRunning()]
        if running_threads:
            reply = QMessageBox.question(self, 'Avsluta?', f'{len(running_threads)} sqlmap-process(er) körs fortfarande...', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                for thread in running_threads: thread.stop(); thread.wait(500)
                self.save_settings()
                event.accept()
            else:
                event.ignore()
        else:
            self.save_settings()
            event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Store the original style name if needed for "Systemstandard" theme
    # This is a simple way; a more robust way might involve platform specifics.
    if not hasattr(SqlmapGui, '_original_style_name'): # Ensure it's set only once
        SqlmapGui._original_style_name = QApplication.style().objectName()

    window = SqlmapGui()
    window.show()
    sys.exit(app.exec())

