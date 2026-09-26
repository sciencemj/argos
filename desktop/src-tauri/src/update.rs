//! Automatic updates (PLAN Phase 12): checks the GitHub release feed shortly after start
//! and every few hours, downloads and installs a newer signed build in the background,
//! and applies it on the next restart (menu bar item, settings, or quitting).

use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::menu::MenuItem;
use tauri::{AppHandle, Manager, Url, Wry};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_updater::UpdaterExt;

const FIRST_CHECK: Duration = Duration::from_secs(20);
const EVERY: Duration = Duration::from_secs(6 * 60 * 60);

#[derive(Clone, Default, Serialize)]
pub struct UpdateState {
    pub current: String,
    pub checking: bool,
    /// A newer version that was found (and is downloading or installed).
    pub available: Option<String>,
    /// Installed; restarting applies it.
    pub ready: bool,
    pub error: Option<String>,
    pub checked_at: Option<u64>, // unix seconds
}

pub struct Updates {
    pub state: Mutex<UpdateState>,
    pub menu_item: Mutex<Option<MenuItem<Wry>>>,
}

impl Updates {
    pub fn new(current: String) -> Self {
        Updates {
            state: Mutex::new(UpdateState {
                current,
                ..Default::default()
            }),
            menu_item: Mutex::new(None),
        }
    }
}

fn now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn set_menu(app: &AppHandle, text: &str) {
    if let Some(item) = app.state::<Updates>().menu_item.lock().unwrap().as_ref() {
        let _ = item.set_text(text);
    }
}

/// Checks once; a found update is downloaded and installed right away. Returns the state.
pub async fn check(app: &AppHandle) -> UpdateState {
    let updates = app.state::<Updates>();
    {
        let mut state = updates.state.lock().unwrap();
        if state.checking || state.ready {
            return state.clone();
        }
        state.checking = true;
        state.error = None;
    }
    set_menu(app, "업데이트 확인 중…");
    let result = run_check(app).await;
    let snapshot = {
        let mut state = updates.state.lock().unwrap();
        state.checking = false;
        state.checked_at = Some(now());
        if let Err(message) = result {
            eprintln!("argos update: {message}");
            state.error = Some(message);
        }
        state.clone()
    };
    match (&snapshot.available, snapshot.ready) {
        (Some(version), true) => set_menu(app, &format!("다시 시작해 업데이트 ({version})")),
        _ => set_menu(app, "업데이트 확인"),
    }
    snapshot
}

async fn run_check(app: &AppHandle) -> Result<(), String> {
    let mut builder = app.updater_builder();
    // For testing a release before it is published: ARGOS_UPDATE_URL=http://…/latest.json
    if let Ok(url) = std::env::var("ARGOS_UPDATE_URL") {
        let url = Url::parse(&url).map_err(|e| e.to_string())?;
        builder = builder.endpoints(vec![url]).map_err(|e| e.to_string())?;
    }
    let updater = builder.build().map_err(|e| e.to_string())?;
    let Some(update) = updater
        .check()
        .await
        .map_err(|e| format!("확인하지 못했어요: {e}"))?
    else {
        return Ok(());
    };
    let version = update.version.clone();
    app.state::<Updates>().state.lock().unwrap().available = Some(version.clone());
    set_menu(app, &format!("업데이트 받는 중 ({version})…"));
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|e| format!("{version} 설치 실패: {e}"))?;
    app.state::<Updates>().state.lock().unwrap().ready = true;
    let _ = app
        .notification()
        .builder()
        .title("Argos 업데이트 준비됨")
        .body(format!("{version} — 메뉴 막대에서 다시 시작하면 적용돼요"))
        .show();
    Ok(())
}

pub fn start(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        tokio_sleep(FIRST_CHECK).await;
        loop {
            check(&app).await;
            tokio_sleep(EVERY).await;
        }
    });
}

async fn tokio_sleep(duration: Duration) {
    let _ = tauri::async_runtime::spawn_blocking(move || std::thread::sleep(duration)).await;
}

/// Restarts into the installed update (stopping the server cleanly first).
pub fn restart(app: &AppHandle) {
    crate::stop_server(app);
    app.restart();
}

// --- commands the settings page calls (desktop.ts) ----------------------------------------

#[tauri::command]
pub fn update_status(app: AppHandle) -> UpdateState {
    app.state::<Updates>().state.lock().unwrap().clone()
}

#[tauri::command]
pub async fn check_update(app: AppHandle) -> UpdateState {
    check(&app).await
}

#[tauri::command]
pub fn restart_to_update(app: AppHandle) {
    if app.state::<Updates>().state.lock().unwrap().ready {
        restart(&app);
    }
}
