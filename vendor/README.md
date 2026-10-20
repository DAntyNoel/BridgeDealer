# DDS source provenance

`dds/` contains the **unmodified native library sources** from
[dds-bridge/dds](https://github.com/dds-bridge/dds), tag **v3.1.0**, commit
[`fe6a6581a13535779eda5d9141aa704b3f6c8965`](https://github.com/dds-bridge/dds/tree/fe6a6581a13535779eda5d9141aa704b3f6c8965).

Included: `library/src/`, upstream `LICENSE`, `README.md`, and `ChangeLog`.
The original source headers and Bazel manifests are preserved. The large upstream
deal datasets, benchmarks, language bindings and documentation are omitted;
the upstream README describes the full upstream repository. No submodule or
additional DDS download is needed to build BridgeDealer.

BridgeDealer's root `CMakeLists.txt` compiles these sources and the pure C shim
(`api/dds_c_api.cpp`) into a shared library. It is an integration wrapper maintained
here, separate from the upstream Bazel build. No upstream C++ file is patched.
The C ABI includes `GetDDSInfo`, `SolveAllBoardsBinN`, and the `dds_c_*` context API.

DDS is distributed under **Apache-2.0**, with the upstream copyright notices and
full license retained in [dds/LICENSE](dds/LICENSE). Copyright Bo Haglund and
Soren Hein; see source headers and upstream history for individual contributions.
