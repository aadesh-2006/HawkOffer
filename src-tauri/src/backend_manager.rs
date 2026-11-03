//! HawkOffer Tauri Desktop Backend Lifecycle & Process Manager.
//!
//! Provides native process supervision for the HawkOffer FastAPI backend:
//! - Resolves bundled sidecar executable or development runtime fallback.
//! - Checks local port availability and single-instance health probe.
//! - Spawns backend process bound strictly to 127.0.0.1.
//! - Performs bounded health readiness polling on startup.
//! - Executes graceful shutdown and cleanup on application termination.

use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;
const READINESS_TIMEOUT_SECS: u64 = 25;
const SHUTDOWN_TIMEOUT_SECS: u64 = 8;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct BackendStatus {
    pub is_running: bool,
    pub is_ready: bool,
    pub pid: Option<u32>,
    pub host: String,
    pub port: u16,
    pub message: String,
    pub data_dir: String,
    pub owns_process: bool,
}

pub struct BackendManager {
    child_process: Arc<Mutex<Option<Child>>>,
    is_shutting_down: Arc<AtomicBool>,
    owns_process: Arc<AtomicBool>,
    spawned_pid: Arc<Mutex<Option<u32>>>,
}

impl Default for BackendManager {
    fn default() -> Self {
        Self::new()
    }
}

impl BackendManager {
    pub fn new() -> Self {
        Self {
            child_process: Arc::new(Mutex::new(None)),
            is_shutting_down: Arc::new(AtomicBool::new(false)),
            owns_process: Arc::new(AtomicBool::new(false)),
            spawned_pid: Arc::new(Mutex::new(None)),
        }
    }

    /// Resolve the cross-platform application data directory (%LOCALAPPDATA% on Windows).
    pub fn resolve_data_dir() -> PathBuf {
        if let Ok(custom) = env::var("HAWKOFFER_DATA_DIR") {
            return PathBuf::from(custom);
        }

        #[cfg(target_os = "windows")]
        {
            if let Ok(local_app_data) = env::var("LOCALAPPDATA") {
                return PathBuf::from(local_app_data).join("HawkOffer").join("HawkOffer");
            }
        }

        #[cfg(target_os = "macos")]
        {
            if let Ok(home) = env::var("HOME") {
                return PathBuf::from(home)
                    .join("Library")
                    .join("Application Support")
                    .join("HawkOffer");
            }
        }

        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        {
            if let Ok(xdg) = env::var("XDG_DATA_HOME") {
                return PathBuf::from(xdg).join("hawkoffer");
            }
            if let Ok(home) = env::var("HOME") {
                return PathBuf::from(home).join(".local").join("share").join("hawkoffer");
            }
        }

        env::temp_dir().join("hawkoffer")
    }

    /// Read PID from the local lockfile if present.
    pub fn read_pid_file() -> Option<u32> {
        let pid_path = Self::resolve_data_dir().join("hawkoffer.pid");
        if pid_path.exists() {
            if let Ok(content) = fs::read_to_string(&pid_path) {
                if let Ok(pid) = content.trim().parse::<u32>() {
                    return Some(pid);
                }
            }
        }
        None
    }

    /// Check if backend is alive and responding with healthy database connection.
    pub fn probe_readiness(timeout: Duration) -> bool {
        let addr = format!("{}:{}", BACKEND_HOST, BACKEND_PORT);
        let stream = match TcpStream::connect_timeout(
            &addr.parse().unwrap_or_else(|_| "127.0.0.1:8765".parse().unwrap()),
            timeout,
        ) {
            Ok(s) => s,
            Err(_) => return false,
        };

        let _ = stream.set_read_timeout(Some(timeout));
        let _ = stream.set_write_timeout(Some(timeout));

        let request = format!(
            "GET /api/v1/health HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
            addr
        );

        let mut stream = stream;
        if stream.write_all(request.as_bytes()).is_err() {
            return false;
        }

        let mut response = Vec::new();
        let _ = stream.read_to_end(&mut response);
        let text = String::from_utf8_lossy(&response);

        text.contains("200 OK") && text.contains("\"database\":\"connected\"")
    }

    /// Locate the backend executable (standalone binary or development venv fallback).
    pub fn locate_backend_binary() -> Option<(PathBuf, Vec<String>)> {
        // 1. Check relative to current executable
        if let Ok(current_exe) = env::current_exe() {
            if let Some(parent) = current_exe.parent() {
                let candidate1 = parent.join("hawkoffer-backend.exe");
                if candidate1.exists() {
                    return Some((candidate1, Vec::new()));
                }

                let candidate2 = parent.join("binaries").join("hawkoffer-backend.exe");
                if candidate2.exists() {
                    return Some((candidate2, Vec::new()));
                }

                let candidate3 = parent.join("hawkoffer-backend");
                if candidate3.exists() {
                    return Some((candidate3, Vec::new()));
                }
            }
        }

        // 2. Check workspace relative paths from Tauri
        let candidate_workspace = Path::new("src-tauri").join("binaries").join("hawkoffer-backend.exe");
        if candidate_workspace.exists() {
            return Some((candidate_workspace.to_path_buf(), Vec::new()));
        }

        let candidate_dist = Path::new("backend").join("dist").join("hawkoffer-backend.exe");
        if candidate_dist.exists() {
            return Some((candidate_dist.to_path_buf(), Vec::new()));
        }

        // 3. Fallback to development Python virtualenv
        let venv_python_win = Path::new("backend").join(".venv").join("Scripts").join("python.exe");
        if venv_python_win.exists() {
            return Some((
                venv_python_win.to_path_buf(),
                vec!["-m".to_string(), "app.bundle_entry".to_string()],
            ));
        }

        let venv_python_unix = Path::new("backend").join(".venv").join("bin").join("python");
        if venv_python_unix.exists() {
            return Some((
                venv_python_unix.to_path_buf(),
                vec!["-m".to_string(), "app.bundle_entry".to_string()],
            ));
        }

        None
    }

    /// Start the backend process if not already running and healthy.
    pub fn start_backend(&self) -> Result<BackendStatus, String> {
        if self.is_shutting_down.load(Ordering::SeqCst) {
            return Err("Cannot start backend: application is shutting down".to_string());
        }

        // 1. If backend is already running and healthy, reuse existing instance
        if Self::probe_readiness(Duration::from_millis(600)) {
            self.owns_process.store(false, Ordering::SeqCst);
            let pid = Self::read_pid_file();
            return Ok(BackendStatus {
                is_running: true,
                is_ready: true,
                pid,
                host: BACKEND_HOST.to_string(),
                port: BACKEND_PORT,
                message: "Existing backend instance verified and healthy".to_string(),
                data_dir: Self::resolve_data_dir().to_string_lossy().to_string(),
                owns_process: false,
            });
        }

        // 2. Locate backend binary
        let (binary_path, base_args) = match Self::locate_backend_binary() {
            Some(res) => res,
            None => {
                return Err(
                    "Could not locate HawkOffer backend standalone binary or development environment"
                        .to_string(),
                );
            }
        };

        // 3. Build arguments
        let mut cmd = Command::new(&binary_path);
        for arg in base_args {
            cmd.arg(arg);
        }

        cmd.arg("--host").arg(BACKEND_HOST);
        cmd.arg("--port").arg(BACKEND_PORT.to_string());

        let data_dir = Self::resolve_data_dir();
        cmd.arg("--data-dir").arg(&data_dir);

        // Hide console window on Windows release builds
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        cmd.stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        // 4. Spawn child process
        let child = cmd
            .spawn()
            .map_err(|e| format!("Failed to spawn backend process ({:?}): {}", binary_path, e))?;

        let pid = child.id();
        {
            let mut lock = self.child_process.lock().map_err(|e| e.to_string())?;
            *lock = Some(child);
        }
        self.owns_process.store(true, Ordering::SeqCst);
        {
            let mut pid_lock = self.spawned_pid.lock().map_err(|e| e.to_string())?;
            *pid_lock = Some(pid);
        }

        // 5. Bounded readiness wait loop
        let start_time = Instant::now();
        let timeout = Duration::from_secs(READINESS_TIMEOUT_SECS);

        while start_time.elapsed() < timeout {
            if Self::probe_readiness(Duration::from_millis(400)) {
                return Ok(BackendStatus {
                    is_running: true,
                    is_ready: true,
                    pid: Some(pid),
                    host: BACKEND_HOST.to_string(),
                    port: BACKEND_PORT,
                    message: format!(
                        "Backend daemon started and ready in {:.1}s",
                        start_time.elapsed().as_secs_f32()
                    ),
                    data_dir: data_dir.to_string_lossy().to_string(),
                    owns_process: true,
                });
            }
            thread::sleep(Duration::from_millis(300));
        }

        Err(format!(
            "Backend spawned (PID {}) but failed readiness probe after {} seconds",
            pid, READINESS_TIMEOUT_SECS
        ))
    }

    /// Stop the backend daemon gracefully.
    pub fn stop_backend(&self) -> Result<(), String> {
        self.is_shutting_down.store(true, Ordering::SeqCst);

        // If we do not own the backend process (e.g. Tauri attached to an existing
        // backend daemon started externally), we must NOT terminate the external
        // process or delete its pidfile on application exit.
        if !self.owns_process.load(Ordering::SeqCst) {
            return Ok(());
        }

        // 1. Stop managed child if we spawned it
        let mut child_opt = {
            let mut lock = self.child_process.lock().map_err(|e| e.to_string())?;
            lock.take()
        };

        if let Some(mut child) = child_opt.take() {
            let pid = child.id();

            #[cfg(target_os = "windows")]
            {
                // Request graceful shutdown via taskkill (without /F)
                let _ = Command::new("taskkill")
                    .args(["/PID", &pid.to_string()])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }

            #[cfg(not(target_os = "windows"))]
            {
                // Send SIGTERM on Unix
                unsafe {
                    libc::kill(pid as i32, libc::SIGTERM);
                }
            }

            // Wait bounded time for process to terminate
            let start = Instant::now();
            let timeout = Duration::from_secs(SHUTDOWN_TIMEOUT_SECS);
            let mut exited = false;

            while start.elapsed() < timeout {
                match child.try_wait() {
                    Ok(Some(_status)) => {
                        exited = true;
                        break;
                    }
                    Ok(None) => thread::sleep(Duration::from_millis(200)),
                    Err(_) => break,
                }
            }

            // Fallback force kill if still running
            if !exited {
                let _ = child.kill();
            }
        }

        // Only clean up PID file if we owned and stopped this backend process
        let pid_path = Self::resolve_data_dir().join("hawkoffer.pid");
        if pid_path.exists() {
            let _ = fs::remove_file(pid_path);
        }

        self.owns_process.store(false, Ordering::SeqCst);
        let mut pid_lock = self.spawned_pid.lock().map_err(|e| e.to_string())?;
        *pid_lock = None;

        Ok(())
    }

    /// Get current diagnostic status of backend.
    pub fn get_status(&self) -> BackendStatus {
        let is_ready = Self::probe_readiness(Duration::from_millis(400));
        let pid = Self::read_pid_file();

        BackendStatus {
            is_running: is_ready || pid.is_some(),
            is_ready,
            pid,
            host: BACKEND_HOST.to_string(),
            port: BACKEND_PORT,
            message: if is_ready {
                "Backend is online and ready".to_string()
            } else {
                "Backend is offline or initializing".to_string()
            },
            data_dir: Self::resolve_data_dir().to_string_lossy().to_string(),
            owns_process: self.owns_process.load(Ordering::SeqCst),
        }
    }
}
