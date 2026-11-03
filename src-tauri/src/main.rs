// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend_manager;

use backend_manager::{BackendManager, BackendStatus};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::Manager;

static SHUTDOWN_INITIATED: AtomicBool = AtomicBool::new(false);

#[tauri::command]
fn get_desktop_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[tauri::command]
fn get_backend_status(state: tauri::State<Arc<BackendManager>>) -> BackendStatus {
    state.get_status()
}

#[tauri::command]
fn restart_backend(state: tauri::State<Arc<BackendManager>>) -> Result<BackendStatus, String> {
    let _ = state.stop_backend();
    std::thread::sleep(std::time::Duration::from_millis(600));
    state.start_backend()
}

#[tauri::command]
fn get_data_directory() -> String {
    BackendManager::resolve_data_dir()
        .to_string_lossy()
        .to_string()
}

fn main() {
    let backend_manager = Arc::new(BackendManager::new());
    let backend_for_setup = backend_manager.clone();
    let backend_for_events = backend_manager.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(backend_manager)
        .invoke_handler(tauri::generate_handler![
            get_desktop_version,
            get_backend_status,
            restart_backend,
            get_data_directory
        ])
        .setup(move |_app| {
            // Automatically launch and verify backend process in background thread
            let backend_init = backend_for_setup.clone();
            std::thread::spawn(move || {
                if let Err(err) = backend_init.start_backend() {
                    eprintln!("HawkOffer Tauri Backend Initialization Warning: {}", err);
                }
            });
            Ok(())
        })
        .on_window_event(move |_window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if !SHUTDOWN_INITIATED.swap(true, Ordering::SeqCst) {
                    let _ = backend_for_events.stop_backend();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running HawkOffer desktop application");
}
