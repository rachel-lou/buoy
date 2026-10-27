//! C ABI between the Meshtastic C++ firmware and Rust.
//!
//! cbindgen turns this file into `include/buoy_rs.h`, so every type and function the C++ side
//! sees must be defined here. Conventions:
//! - Plain data crosses as primitives, or `#[repr(C)]` structs passed by pointer.
//! - Rust-owned state is an opaque handle (`Buoy`): C++ gets it from `buoy_new` and must give
//!   it back to `buoy_free`. C++ never sees its layout.
//! - Rust calls back into C++ through a table of function pointers (`BuoyHostApi`) handed over
//!   at construction, not link-time `extern "C"` symbols. Keeps the dependency one-way and lets
//!   tests swap in a fake host.
//! - Strings cross as (pointer, length) pairs of UTF-8 bytes, not NUL-terminated.

use core::ffi::c_void;

use crate::app::{App, Host, LogLevel};

/// Callbacks into the firmware. Every function pointer must be non-null. `ctx` is passed back
/// unchanged on every call (the C++ module's `this`).
#[repr(C)]
pub struct BuoyHostApi {
    pub ctx: *mut c_void,
    /// `level`: 0 debug, 1 info, 2 warn, 3 error. `msg` is UTF-8, not NUL-terminated.
    pub log: extern "C" fn(ctx: *mut c_void, level: u8, msg: *const u8, len: usize),
    pub millis: extern "C" fn(ctx: *mut c_void) -> u32,
    pub send_text: extern "C" fn(ctx: *mut c_void, to: u32, msg: *const u8, len: usize) -> bool,
}

impl Host for BuoyHostApi {
    fn log(&self, level: LogLevel, msg: &str) {
        (self.log)(self.ctx, level as u8, msg.as_ptr(), msg.len())
    }
    fn millis(&self) -> u32 {
        (self.millis)(self.ctx)
    }
    fn send_text(&mut self, to: u32, msg: &str) -> bool {
        (self.send_text)(self.ctx, to, msg.as_ptr(), msg.len())
    }
}

/// Opaque handle to the Rust side's state.
pub struct Buoy(App<BuoyHostApi>);

/// Creates the Rust side. Free it with `buoy_free`.
#[no_mangle]
pub extern "C" fn buoy_new(host: BuoyHostApi) -> *mut Buoy {
    alloc::boxed::Box::into_raw(alloc::boxed::Box::new(Buoy(App::new(host))))
}

/// # Safety
/// `buoy` must come from `buoy_new` (or be null) and not be used afterwards.
#[no_mangle]
pub unsafe extern "C" fn buoy_free(buoy: *mut Buoy) {
    if !buoy.is_null() {
        drop(alloc::boxed::Box::from_raw(buoy));
    }
}

/// Runs one periodic step. Returns milliseconds until the next call is wanted.
///
/// # Safety
/// `buoy` must be valid.
#[no_mangle]
pub unsafe extern "C" fn buoy_tick(buoy: *mut Buoy) -> u32 {
    (*buoy).0.tick()
}

/// Offers a received mesh text message to Rust. Returns true if Rust handled it.
///
/// # Safety
/// `buoy` must be valid; `text` must be valid for `len` bytes.
#[no_mangle]
pub unsafe extern "C" fn buoy_on_text(buoy: *mut Buoy, from: u32, text: *const u8, len: usize) -> bool {
    match core::str::from_utf8(core::slice::from_raw_parts(text, len)) {
        Ok(s) => (*buoy).0.on_text(from, s),
        Err(_) => false,
    }
}
