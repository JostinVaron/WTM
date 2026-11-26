//! Pen tablet fields that winit drops on Linux X11 (pressure, tilt, rotation).
//!
//! winit delivers the pointer, but the XInput2 tablet valuators never reach
//! egui-winit. `rust-motion-tablet` listens on a second pure-Rust X
//! connection (raw XI2 events, also during a drag grab) and hands us the pen
//! sample. Every app built on this shell already reads pressure as
//! `egui::Event::Touch { force }`, so the driver injects that event before
//! egui processes the frame. Tilt, rotation and eraser stay available in the
//! sample for brush engines that consume them.
//!
//! Delivery is one shared point for the whole suite (PhotoCraft, VectorCraft,
//! FilmCraft, EffectCraft). The LightCraft app owns its own `eframe::App`, so
//! it carries the same module app-level.

use crate::runtime::egui;

#[cfg(target_os = "linux")]
mod imp {
    use super::egui;
    use rust_motion_tablet::Sample;
    use std::sync::{
        Mutex, Once, OnceLock,
        mpsc::{self, Receiver},
    };

    static START: Once = Once::new();
    static RX: OnceLock<Mutex<Receiver<Option<Sample>>>> = OnceLock::new();
    static LAST_POS: Mutex<Option<egui::Pos2>> = Mutex::new(None);

    /// Starts the XInput2 listener once. Safe to call every frame.
    pub fn start() {
        START.call_once(|| {
            let (tx, rx) = mpsc::channel();
            if RX.set(Mutex::new(rx)).is_err() {
                return;
            }
            match rust_motion_tablet::x11::spawn(None, move |sample| {
                let _ = tx.send(sample);
            }) {
                Ok(_thread) => eprintln!("[pen] XInput2 tablet bridge started"),
                Err(e) => eprintln!("[pen] tablet bridge unavailable: {e}"),
            }
        });
    }

    /// Appends the latest pen sample to the frame's raw input.
    pub fn inject(raw: &mut egui::input::RawInput) {
        // The pen sample carries no position: pair it with the pointer the
        // host delivered this frame (the pen is a pointer for winit).
        if let Some(pos) = raw.events.iter().rev().find_map(|e| match e {
            egui::Event::PointerMoved(pos) => Some(*pos),
            egui::Event::PointerButton { pos, .. } => Some(*pos),
            _ => None,
        }) {
            if let Ok(mut last) = LAST_POS.lock() {
                *last = Some(pos);
            }
        }

        let Some(rx) = RX.get() else {
            return;
        };
        let mut latest: Option<Option<Sample>> = None;
        if let Ok(rx) = rx.lock() {
            while let Ok(sample) = rx.try_recv() {
                latest = Some(sample);
            }
        }
        // Some(sample): pen in use. None: the pointer became a mouse again.
        let Some(Some(sample)) = latest else {
            return;
        };
        let sample = sample.sanitized();
        let pos = LAST_POS
            .lock()
            .ok()
            .and_then(|p| *p)
            .or_else(|| raw.screen_rect.map(|r| r.center()))
            .unwrap_or(egui::Pos2::ZERO);
        raw.events.push(egui::Event::Touch {
            device_id: egui::TouchDeviceId(0x57544d), // "WTM"
            id: egui::TouchId(1),
            phase: egui::TouchPhase::Move,
            pos,
            force: Some(sample.pressure),
        });
        // Tilt, rotation and eraser are available for brush engines that use
        // them; the sample is dropped after this frame otherwise.
        let _ = (sample.tilt_x, sample.tilt_y, sample.rotation, sample.eraser);
    }
}

#[cfg(target_os = "linux")]
pub use imp::{inject, start};

#[cfg(not(target_os = "linux"))]
pub fn start() {}

#[cfg(not(target_os = "linux"))]
pub fn inject(_raw: &mut crate::runtime::egui::input::RawInput) {}
