// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

static SHUTDOWN_INITIATED: AtomicBool = AtomicBool::new(false);

#[tauri::command]
fn get_desktop_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

fn main() {
    let shutdown_flag = Arc::new(AtomicBool::new(false));
    let shutdown_flag_clone = shutdown_flag.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![get_desktop_version])
        .on_window_event(move |_window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if !shutdown_flag_clone.swap(true, Ordering::SeqCst) {
                    SHUTDOWN_INITIATED.store(true, Ordering::SeqCst);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running HawkOffer desktop application");
}
