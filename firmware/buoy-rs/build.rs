// Regenerates include/buoy_rs.h (the C header the C++ side includes) from src/ffi.rs.
fn main() {
    println!("cargo:rerun-if-changed=src/ffi.rs");
    println!("cargo:rerun-if-changed=cbindgen.toml");
    let dir = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    let config = cbindgen::Config::from_file(format!("{dir}/cbindgen.toml")).unwrap();
    cbindgen::Builder::new()
        .with_config(config)
        .with_src(format!("{dir}/src/ffi.rs"))
        .generate()
        .expect("cbindgen failed")
        .write_to_file(format!("{dir}/include/buoy_rs.h"));
}
