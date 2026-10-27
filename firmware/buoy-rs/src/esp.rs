//! ESP32-only runtime glue. Rust shares the firmware's ESP-IDF heap and reports panics through
//! the ROM printf before aborting (which makes ESP-IDF print a backtrace and reboot).

use core::alloc::{GlobalAlloc, Layout};
use core::ffi::{c_char, c_int, c_void};
use core::fmt::Write;

extern "C" {
    fn malloc(size: usize) -> *mut c_void;
    fn realloc(ptr: *mut c_void, size: usize) -> *mut c_void;
    fn free(ptr: *mut c_void);
    fn heap_caps_aligned_alloc(alignment: usize, size: usize, caps: u32) -> *mut c_void;
    fn esp_rom_printf(fmt: *const c_char, ...) -> c_int;
    fn abort() -> !;
}

const MALLOC_CAP_DEFAULT: u32 = 1 << 12;
/// ESP-IDF malloc guarantees 4-byte alignment.
const MALLOC_ALIGN: usize = 4;

struct EspHeap;

unsafe impl GlobalAlloc for EspHeap {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if layout.align() <= MALLOC_ALIGN {
            malloc(layout.size()) as *mut u8
        } else {
            heap_caps_aligned_alloc(layout.align(), layout.size(), MALLOC_CAP_DEFAULT) as *mut u8
        }
    }

    // In ESP-IDF 5, free() also releases heap_caps_aligned_alloc() blocks.
    unsafe fn dealloc(&self, ptr: *mut u8, _layout: Layout) {
        free(ptr as *mut c_void)
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        if layout.align() <= MALLOC_ALIGN {
            return realloc(ptr as *mut c_void, new_size) as *mut u8;
        }
        let new = self.alloc(Layout::from_size_align_unchecked(new_size, layout.align()));
        if !new.is_null() {
            core::ptr::copy_nonoverlapping(ptr, new, layout.size().min(new_size));
            self.dealloc(ptr, layout);
        }
        new
    }
}

#[global_allocator]
static HEAP: EspHeap = EspHeap;

/// Fixed-size, truncating formatter so panicking never allocates.
struct StackBuf {
    buf: [u8; 192],
    len: usize,
}

impl Write for StackBuf {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        let room = self.buf.len() - 1 - self.len; // keep one byte for the NUL
        let n = s.len().min(room);
        self.buf[self.len..self.len + n].copy_from_slice(&s.as_bytes()[..n]);
        self.len += n;
        Ok(())
    }
}

#[panic_handler]
fn panic(info: &core::panic::PanicInfo) -> ! {
    let mut msg = StackBuf { buf: [0; 192], len: 0 };
    let _ = write!(msg, "{info}");
    msg.buf[msg.len] = 0;
    unsafe {
        esp_rom_printf(c"\n*** Rust panic: %s\n".as_ptr(), msg.buf.as_ptr());
        abort()
    }
}
