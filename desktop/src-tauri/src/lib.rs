//! Argos desktop app (PLAN Phase 12). Starts the Argos server (a PyInstaller sidecar),
//! shows it in a window once it answers, and stays in the menu bar when the window is
//! closed so other devices, sync, jobs and notices keep working.

mod update;

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const PORT: u16 = 8000;
const QUICK_SHORTCUT: &str = "CommandOrControl+Shift+Space";
const START_TIMEOUT: Duration = Duration::from_secs(90);

/// Whether Argos' main window had focus when quick capture opened: closing it then goes
/// back there, otherwise to whatever app the user was in.
struct QuickFrom(Mutex<bool>);

/// The running server, so it can be stopped when the app quits.
struct Server(Mutex<Option<CommandChild>>);

fn base_url() -> String {
    format!("http://127.0.0.1:{PORT}")
}

fn data_dir() -> PathBuf {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join("Library/Application Support/Argos")
}

/// Is an Argos server answering on the port? (a plain HTTP/1.0 GET, no client library)
fn argos_answers() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], PORT));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
    let request = "GET /api/v1/config HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n";
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut reply = String::new();
    let _ = stream.read_to_string(&mut reply);
    reply.starts_with("HTTP/1.1 200") && reply.contains("\"timezone\"")
}

fn port_taken() -> bool {
    TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], PORT)),
        Duration::from_millis(300),
    )
    .is_ok()
}

/// Shows a line on the start page (bundled index.html) while the server starts.
fn status(app: &AppHandle, text: &str, failed: bool) {
    eprintln!("argos: {text}");
    if let Some(window) = app.get_webview_window("main") {
        let text = serde_json::to_string(text).unwrap_or_default();
        let _ = window.eval(format!(
            "window.setStatus && window.setStatus({text}, {failed})"
        ));
    }
}

fn the_app_bundle() -> Option<PathBuf> {
    // …/Argos.app/Contents/MacOS/argos-desktop → …/Argos.app
    let exe = std::env::current_exe().ok()?;
    let bundle = exe.parent()?.parent()?.parent()?;
    (bundle.extension()? == "app").then(|| bundle.to_path_buf())
}

fn start_server(app: &AppHandle) -> Result<(), String> {
    if argos_answers() {
        // Another Argos (a previous run, or `make dev` on the old port) already serves.
        return Ok(());
    }
    if port_taken() {
        return Err(format!(
            "포트 {PORT}을 다른 프로그램이 쓰고 있어요. 그 프로그램을 끄고 Argos를 다시 열어 주세요."
        ));
    }
    let mut command = app
        .shell()
        .sidecar("argos-server")
        .map_err(|e| format!("서버 파일을 찾지 못했어요: {e}"))?
        .env("ARGOS_DATA_DIR", data_dir())
        .env("ARGOS_PORT", PORT.to_string())
        .env("ARGOS_PARENT_PID", std::process::id().to_string());
    if let Some(bundle) = the_app_bundle() {
        command = command.env("ARGOS_DESKTOP_APP", bundle);
    }
    let (mut events, child) = command
        .spawn()
        .map_err(|e| format!("서버를 시작하지 못했어요: {e}"))?;
    app.state::<Server>().0.lock().unwrap().replace(child);

    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            if let CommandEvent::Terminated(done) = event {
                handle.state::<Server>().0.lock().unwrap().take();
                let log = data_dir().join("logs/server.log");
                status(
                    &handle,
                    &format!(
                        "서버가 멈췄어요 (코드 {:?}). 기록: {}",
                        done.code,
                        log.display()
                    ),
                    true,
                );
                show_main(&handle, None);
            }
        }
    });
    Ok(())
}

/// Asks the server to stop (SIGTERM lets uvicorn finish writes and close the database),
/// then forces it after a few seconds.
pub(crate) fn stop_server(app: &AppHandle) {
    let Some(child) = app.state::<Server>().0.lock().unwrap().take() else {
        return;
    };
    let _ = std::process::Command::new("/bin/kill")
        .args(["-TERM", &child.pid().to_string()])
        .status();
    let started = Instant::now();
    while port_taken() && started.elapsed() < Duration::from_secs(5) {
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
}

/// Waits for the server, then points both windows at it.
fn open_when_ready(app: AppHandle) {
    std::thread::spawn(move || {
        let started = Instant::now();
        while !argos_answers() {
            if started.elapsed() > START_TIMEOUT {
                status(
                    &app,
                    "서버가 제때 뜨지 않았어요. 기록을 확인해 주세요.",
                    true,
                );
                return;
            }
            let stopped = app.state::<Server>().0.lock().unwrap().is_none();
            if stopped && started.elapsed() > Duration::from_secs(2) {
                return; // it stopped; the termination handler already said so
            }
            std::thread::sleep(Duration::from_millis(300));
        }
        let base = base_url();
        if let Some(main) = app.get_webview_window("main") {
            if let Ok(url) = Url::parse(&format!("{base}/")) {
                let _ = main.navigate(url);
            }
        }
        if let Some(quick) = app.get_webview_window("quick") {
            if let Ok(url) = Url::parse(&format!("{base}/quick")) {
                let _ = quick.navigate(url);
            }
        }
    });
}

#[cfg(target_os = "macos")]
fn dock_icon(app: &AppHandle, visible: bool) {
    use tauri::ActivationPolicy;
    let policy = if visible {
        ActivationPolicy::Regular
    } else {
        ActivationPolicy::Accessory
    };
    let _ = app.set_activation_policy(policy);
}

#[cfg(not(target_os = "macos"))]
fn dock_icon(_app: &AppHandle, _visible: bool) {}

fn show_main(app: &AppHandle, path: Option<&str>) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    if let Some(path) = path {
        if let Ok(url) = Url::parse(&format!("{}{path}", base_url())) {
            let _ = window.navigate(url);
        }
    }
    dock_icon(app, true);
    let _ = window.unminimize();
    let _ = window.show();
    let _ = window.set_focus();
}

fn toggle_quick(app: &AppHandle) {
    let Some(quick) = app.get_webview_window("quick") else {
        return;
    };
    if quick.is_visible().unwrap_or(false) {
        close_quick(app);
    } else {
        let main_focused = app
            .get_webview_window("main")
            .and_then(|w| w.is_focused().ok())
            .unwrap_or(false);
        *app.state::<QuickFrom>().0.lock().unwrap() = main_focused;
        let _ = quick.center();
        let _ = quick.show();
        let _ = quick.set_focus();
    }
}

/// Hides quick capture and gives focus back: to Argos if it was in front, else to the
/// previous app (hiding Argos hands the focus back, as Spotlight does).
fn close_quick(app: &AppHandle) {
    let Some(quick) = app.get_webview_window("quick") else {
        return;
    };
    let _ = quick.hide();
    if *app.state::<QuickFrom>().0.lock().unwrap() {
        if let Some(main) = app.get_webview_window("main") {
            let _ = main.set_focus();
        }
    } else {
        #[cfg(target_os = "macos")]
        let _ = app.hide();
    }
}

fn quick_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    WebviewWindowBuilder::new(app, "quick", WebviewUrl::App("index.html".into()))
        .title("Argos 빠른 입력")
        .inner_size(560.0, 132.0)
        .resizable(false)
        .decorations(false)
        .transparent(true) // the page draws a rounded card; the corners stay see-through
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .visible(false)
        .center()
        .build()
}

// --- commands the Argos pages call (desktop.ts) -------------------------------------------

#[tauri::command]
fn notify(app: AppHandle, title: String, body: String) {
    let _ = app.notification().builder().title(title).body(body).show();
}

#[tauri::command]
fn hide_quick(app: AppHandle) {
    close_quick(&app);
}

#[tauri::command]
fn open_main(app: AppHandle, path: String) {
    let path = if path.starts_with('/') {
        path
    } else {
        format!("/{path}")
    };
    if let Some(quick) = app.get_webview_window("quick") {
        let _ = quick.hide(); // opened from quick capture: that window goes away
    }
    show_main(&app, Some(&path));
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main(app, None); // opened again: bring the window back
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        toggle_quick(app);
                    }
                })
                .build(),
        )
        .manage(Server(Mutex::new(None)))
        .manage(QuickFrom(Mutex::new(false)))
        .invoke_handler(tauri::generate_handler![
            notify,
            hide_quick,
            open_main,
            update::update_status,
            update::check_update,
            update::restart_to_update
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            quick_window(&handle)?;

            let open = MenuItem::with_id(app, "open", "Argos 열기", true, None::<&str>)?;
            let quick = MenuItem::with_id(app, "quick", "빠른 입력  ⌘⇧Space", true, None::<&str>)?;
            let logs = MenuItem::with_id(app, "logs", "기록 폴더 열기", true, None::<&str>)?;
            let check = MenuItem::with_id(app, "update", "업데이트 확인", true, None::<&str>)?;
            app.manage(update::Updates::new(app.package_info().version.to_string()));
            app.state::<update::Updates>()
                .menu_item
                .lock()
                .unwrap()
                .replace(check.clone());
            let quit = MenuItem::with_id(app, "quit", "Argos 종료", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let menu = Menu::with_items(app, &[&open, &quick, &separator, &check, &logs, &quit])?;
            let tray_icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray.png"))?;
            TrayIconBuilder::with_id("argos")
                .icon(tray_icon)
                .icon_as_template(true)
                .tooltip("Argos")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open" => show_main(app, None),
                    "quick" => toggle_quick(app),
                    "logs" => {
                        let _ = std::process::Command::new("/usr/bin/open")
                            .arg(data_dir().join("logs"))
                            .spawn();
                    }
                    "update" => {
                        if app.state::<update::Updates>().state.lock().unwrap().ready {
                            update::restart(app);
                        } else {
                            let app = app.clone();
                            tauri::async_runtime::spawn(async move {
                                update::check(&app).await;
                            });
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main(tray.app_handle(), None);
                    }
                })
                .build(app)?;

            match QUICK_SHORTCUT.parse::<Shortcut>() {
                Ok(shortcut) => {
                    if let Err(e) = app.global_shortcut().register(shortcut) {
                        eprintln!("quick-capture shortcut unavailable: {e}");
                    }
                }
                Err(e) => eprintln!("bad shortcut: {e}"),
            }

            update::start(handle.clone());
            if let Err(message) = start_server(&handle) {
                status(&handle, &message, true);
            } else {
                open_when_ready(handle);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Closing hides: the server keeps serving the iPad, sync and notices.
                api.prevent_close();
                let _ = window.hide();
                if window.label() == "main" {
                    dock_icon(window.app_handle(), false);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the Argos app");

    app.run(|app, event| match event {
        RunEvent::Exit => stop_server(app),
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => show_main(app, None), // Dock icon clicked
        _ => {}
    });
}
