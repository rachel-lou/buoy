//! Buoy logic: pure Rust, no FFI. Everything that touches the outside world goes through the
//! `Host` trait, which the firmware implements via C++ callbacks (see `ffi.rs`) and tests
//! implement with a fake. Stub for now: logs a hello on each tick and answers `buoy ping`.

use alloc::format;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum LogLevel {
    Debug = 0,
    Info = 1,
    Warn = 2,
    Error = 3,
}

/// Services the firmware provides to the Rust side.
pub trait Host {
    fn log(&self, level: LogLevel, msg: &str);
    fn millis(&self) -> u32;
    /// Send a text message over the mesh. `to` is a node number (0xFFFFFFFF = broadcast).
    fn send_text(&mut self, to: u32, msg: &str) -> bool;
}

const TICK_MS: u32 = 30_000;

pub struct App<H: Host> {
    host: H,
    ticks: u32,
}

impl<H: Host> App<H> {
    pub fn new(host: H) -> Self {
        host.log(LogLevel::Info, "buoy-rs: started");
        Self { host, ticks: 0 }
    }

    /// Called periodically by the firmware. Returns milliseconds until the next tick.
    pub fn tick(&mut self) -> u32 {
        self.ticks += 1;
        self.host.log(
            LogLevel::Info,
            &format!("hello from Rust: tick {} at {} ms", self.ticks, self.host.millis()),
        );
        TICK_MS
    }

    /// Handle a mesh text message. Returns true if it was meant for us.
    pub fn on_text(&mut self, from: u32, text: &str) -> bool {
        if !text.trim().eq_ignore_ascii_case("buoy ping") {
            return false;
        }
        let reply = format!("pong from Rust (tick {})", self.ticks);
        if !self.host.send_text(from, &reply) {
            self.host.log(LogLevel::Warn, "reply failed to send");
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::string::String;
    use std::vec::Vec;

    #[derive(Default)]
    struct FakeHost {
        sent: Vec<(u32, String)>,
    }

    impl Host for &mut FakeHost {
        fn log(&self, _level: LogLevel, _msg: &str) {}
        fn millis(&self) -> u32 {
            0
        }
        fn send_text(&mut self, to: u32, msg: &str) -> bool {
            self.sent.push((to, msg.into()));
            true
        }
    }

    #[test]
    fn ping_replies_to_sender_and_ignores_other_text() {
        let mut host = FakeHost::default();
        let mut app = App::new(&mut host);
        app.tick();
        assert!(app.on_text(0xabcd, "Buoy Ping"));
        assert!(!app.on_text(0xabcd, "ALL 2H"));
        drop(app);
        assert_eq!(host.sent, vec![(0xabcd, "pong from Rust (tick 1)".into())]);
    }
}
