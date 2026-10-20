# Third-party software

BridgeDealer's original code is MIT licensed. Third-party components retain
their own licenses:

| Component | Version | Use | License / source |
| --- | --- | --- | --- |
| DDS | 3.1.0, `fe6a6581a13535779eda5d9141aa704b3f6c8965` | Vendored native solver and C API | [Apache-2.0](vendor/dds/LICENSE), [source provenance](vendor/README.md) |
| endplay | 0.5.12 | v1 seeded bridge deal generation | [MIT](https://github.com/dominicprice/endplay/blob/main/LICENSE) |
| Google OR-Tools | 9.15.6755 | CP-SAT feasibility and exhaustive enumeration | [Apache-2.0](https://github.com/google/or-tools/blob/stable/LICENSE) |

Python dependencies and their transitive dependencies are installed from the
versions recorded in `uv.lock`; their distributions include their own license
notices. They are not copied into this source repository. The endplay package
includes its own native DDS component; BridgeDealer's success queries explicitly
use the separately built **DDS 3.1.0** library in `lib/`.

The UI is original HTML/CSS/JavaScript. It implements familiar bridge table and
contract controls without bundling assets, logos or code from 新睿桥牌.
