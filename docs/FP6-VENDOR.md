# FP6 vendor files and product

How the build takes the Fairphone 6's closed vendor files from the stock factory package, and what
the device tree and the generated vendor tree rely on.

For people who change the vendor recipes or the device tree. The `vendor` step of [the
build](BUILD.md#the-build-steps) runs all of this for you.

Terms are explained in [TERMS.md](TERMS.md). The firmware has its own document,
[FIRMWARE.md](FIRMWARE.md).

## The stock input

Every generated vendor file comes from one exact, hash-pinned factory package:

- [`config/stock-inputs.json`](../config/stock-inputs.json) selects it (`selected_generator_input`)
  and records where Fairphone publishes it. The selected package is the EU one. The US package listed
  there as excluded is never an input.
- [`config/fp6-stock-image-recipe.json`](../config/fp6-stock-image-recipe.json) pins the archive and
  each image in it by size and SHA-256.
- [`config/fp6-minimal/vendor-files.json`](../config/fp6-minimal/vendor-files.json) selects the stock
  files, and `vendor-elf.json` next to it binds their reviewed dependencies.

The tools read the factory package only, never a phone. Calibration, identity and provisioning state
never enter a generated tree. Vendor files and firmware come from the same stock release.

## Running the steps by hand

The `vendor` step runs these commands. They stay available for inspection and for work on one input.
No command accesses a phone. Keep the work directories outside source repositories, on a
case-sensitive filesystem.

```sh
TOOLS_ROOT="$PWD"
WORK_ROOT=/absolute/path/to/work
FACTORY_ZIP=/absolute/path/to/factory.zip
IMAGE_TOOLS=/absolute/path/to/image-tools/bin
AAPT2=/absolute/path/to/aapt2
NOTICE_KIND=<notice_kind from config/fp6-build.json>
SOURCE_ROOT=/absolute/path/to/android-source

"$TOOLS_ROOT/bin/diamaneos" vendor stage FP6 --archive "$FACTORY_ZIP" \
  --output "$WORK_ROOT/stock-images"
"$TOOLS_ROOT/bin/diamaneos" vendor extract FP6 \
  --super "$WORK_ROOT/stock-images/current/super.img" \
  --image-tools "$IMAGE_TOOLS" --output "$WORK_ROOT/stock-files" [--record-tools]
"$TOOLS_ROOT/bin/diamaneos" vendor product FP6 \
  --inputs "$(realpath "$WORK_ROOT/stock-files/current")" \
  --output "$WORK_ROOT/vendor-product" --notice-kind "$NOTICE_KIND" --aapt2 "$AAPT2" \
  --factory-zip "$FACTORY_ZIP"
"$TOOLS_ROOT/bin/diamaneos" build inputs FP6 --source "$SOURCE_ROOT" \
  --vendor "$WORK_ROOT/vendor-product"
```

- `FACTORY_ZIP` is the exact archive the stock image recipe pins. Other regions or builds are not
  interchangeable.
- `IMAGE_TOOLS` is a `bin` directory with `simg2img`, `lpunpack` and `debugfs_static` and a `lib64`
  directory next to it. Either use an otatools package whose files `config/fp6-image-tools.json` pins,
  or the synced source's `out/host/linux-x86/bin` with `--record-tools`, which records their hashes
  instead.
- `AAPT2` decodes the stock carrier configuration ([CARRIER-INTEGRATION.md](CARRIER-INTEGRATION.md)).
  Its hash goes into the provenance.
- Make sure no build or sync runs in the Android checkout before `build inputs`.

## What each command does

**`vendor stage`** authenticates the factory package and the selected images. It takes only the
system container, boot, DTBO and AVB images, never userdata, persist, provisioning or modem state. It
runs no factory script.

**`vendor extract`** authenticates `super.img`, unpacks only the logical partitions the recipe reads
and dumps the declared regular files with the pinned ext4 reader. It checks each file's size and
hash, each symlink and the notices.

It mounts nothing. The stock system partition is read for two reviewed files only: the interface
library of the camera algorithm service and Fairphone's camera APK.

**`vendor product`** checks the component policy, hashes, dependency edges and activation files. It
applies the reviewed source replacements and derived configuration, and writes the generated Android
integration with its provenance and complete inventory.

All three publish content-addressed generations with a `current` link. Identical inputs reuse a
generation, and a failure never replaces `current`. Unsafe archive members, symlink tricks, special
files and changed generated contents are refused.

**`build inputs`** verifies that inventory and installs the tree at `vendor/fairphone/FP6`, which a
manifest sync alone does not fill. It writes the generated-input descriptor that the source check reads
([build records](BUILD.md#build-records-and-identity)).

`--replace` moves a differing installed tree aside instead of refusing it. Do not edit the generated
tree by hand.

**`vendor generate`** makes only the selected files of a recipe that follows
`schemas/vendor-files.schema.json`, without the product:

```sh
"$TOOLS_ROOT/bin/diamaneos" vendor generate FP6 --recipe /absolute/path/to/selected-files.json \
  --inputs /absolute/path/to/extracted-partitions --output /absolute/path/to/generated-vendor
```

The input directory holds partition directories named `vendor`, `odm`, `system`, `system_ext` or
`product`. The recipe names its stock build and archive hash, which must match the stock image recipe
(`--stock` selects another). Original owner, mode, SELinux label and capabilities stay metadata in the
output's `manifest.json`.

## How stock files are selected

Each selected file has an exact hash, length, stock origin, component, purpose, dependency list and
notices. The selection follows the ELF dependencies of each file. A few cases need more than that.

**Libraries loaded at run time.** A file may declare `runtime_dependencies`: selected files it loads
with `dlopen` rather than links. `vendor-elf.json` mirrors each one as a `selected-stock-runtime`
edge, and the two must agree.

For example, qseecomd loads its secure-world listener libraries this way. Without them it exits,
KeyMint cannot serve vold and `/data` never mounts.

**Source-built libraries.** Stock blobs link some libraries that the device builds from source:
frozen AIDL and HIDL interfaces, the source-built display stack and a few AOSP libraries such as
`libdrm`.

In the other direction, the source-built audio stack links a few stock libraries
(`SOURCE_LINKED_STOCK` in `vendor_product.py`), which are installed under their own names.

A C++ library that only a closed HAL uses stays stock. The ELF checks cannot catch a class layout
change, so the stock KeyMint HAL keeps its own `libkeymaster_messages`.

The same caveat applies where closed code links a source-built display library, such as CamX against
`libgralloc.qti`.

**Renamed dependencies.** Some stock blobs get one dependency renamed in place, to a device library
with a name of the same length, and both hashes are pinned:

- The display colour manager links an old-ABI tinyxml2 copy.
- The camera provider links the device's seccomp loader, which installs its system call filter before
  the provider's `main()`.
- The lens distortion node (`com.fp.node.distortion.so`) of the two combined cameras was built for an
  older Android, where `GraphicBuffer` is 256 bytes. Its `libui.so` becomes the device's `uiv34.so`,
  which defines `GraphicBufV34` at the old size, so the current, larger class cannot overflow the
  provider's heap.

The image checks confirm each renamed import. Source-built replacements and uninstalled optional
libraries are listed in the generated provenance.

## Special cases

**Widevine.** The one stock APEX is the Widevine DRM HAL (`STOCK_APEXES` in `vendor_product.py`),
installed whole with its own signatures. It runs with its built-in software protection only, in its
own `hal_drm_widevine_sw` domain.

The hardware-backed path, its TEE libraries and its provisioning tools are not selected. The
`apex_payload` and Widevine rules of `config/fp6-image-checks.json` check the package, its service
definition and its SELinux access.

**Video codecs.** The stock codec list and Codec2 target specification are installed as two pinned
derivations, and the device picks one at each boot from the user's hardware video decoding choice:

| Choice | Codecs |
| --- | --- |
| Never (the default) | The five hardware encoders, no decoder |
| All video | The encoders and the three clear decoders |
| Selected apps | As all video, and the codec service checks each caller |

No list has a secure or a low-latency decoder. The `media-hw-decode-*` image checks cover the states
and who may set them.

**Vendor patch level.** `VENDOR_SECURITY_PATCH` in the generated `BoardConfigVendor.mk` is the value
the stock `vendor/build.prop` sets. That file is pinned under `build_properties` and read, never
installed. The date must appear exactly once and be no later than the package's release date in
`config/stock-inputs.json`.

**Carrier data.** The stock carrier configuration and the IWLAN apps are described in
[CARRIER-INTEGRATION.md](CARRIER-INTEGRATION.md).

## Fairphone Camera

Fairphone's camera app (`com.fps.camera`) is not in the image. The App Store installs it. The app
uses the stock camera libraries and algorithm service of the build, so the APK that fits a build is
the one its stock release ships.

- The recipe pins that APK (`/system/priv-app/FPCamera/FPCamera.apk` in the stock image) by size and
  SHA-256 under `catalog_apps`. Extraction and generation check it. It is never installed.
- Generation writes `de.diamaneos.fairphone_camera.app.xml` to `/vendor/etc/permissions`. It declares
  the feature `de.diamaneos.fairphone_camera.apk_` followed by the first 16 hex digits of the APK's
  SHA-256.
- A catalog version of the app lists the feature of its own APK. The App Store offers a version only
  on a build that declares every feature the version lists.
- `build verify` requires exactly one such feature: the one of the pinned APK
  (`fpcamera-app-apk-feature`).

**Moving to another stock release** includes the app:

1. Pin the new package's camera APK under `catalog_apps`. The vendor step stops while the stock image
   holds another APK than the pinned one.
2. Test the app on a phone with the new build, also when the APK is the same: photo, video, 60 fps
   video, portrait, night and HDR, and no network access for the app.
3. The same APK keeps its feature and needs no new catalog version. Another APK gets its own catalog
   version, published with that OS release.

## Open data stack

The device builds the data daemon `datad` and the libraries `libnicm` and `libnicm_dsi` from source
(`hardware/diamaneos/datad`), in place of the stock `nicmd` and its libraries.

- The vendor step selects the device's recipe and ELF selection through `datad_selection.transform()`.
  It removes the twelve stock files of `nicmd` and its libraries, and the edges of
  `libdsi_netctrl.so` to the two libraries become edges to the source-built modules.
- It stops when the stock files are not the ones it was written for, so a stock update gets a review
  before the next build.
- The vendor tree's provenance says `datad_selection`, and a tree that does not is refused.
- The `data-stack` and `vendor-binding` image checks require the daemon and the libraries, and none of
  the stock files.

## FP6 product

What the device tree (`device/fairphone/FP6`) and the generated vendor tree rely on.

**Source-built parts.** The device tree owns the device policy and hardware setup. It builds boot
control, power, thermal, lights, vibrator, USB and health services from source.

- The power HAL is LineageOS's libperfmgr with the device's `power/` configuration. Qualcomm's perf
  daemon is not selected. The device's `libqti-perfd-client` passes only the camera's hints to the
  power HAL, as short boosts.
- The power stats HAL is the device's own (`power/stats`): sleep residency from the qcom_stats driver,
  without energy meters, which stock has none of.
- The stock thermal engine is a separately selected input. The thermal HAL is built from source.
- The product uses the generic first-stage ramdisk, GKI v4 headers, protected VM firmware (`pvmfw`)
  built from source, 4 KiB pages and a 48-bit virtual address space
  ([FP6-KERNEL.md](FP6-KERNEL.md#deliberate-settings)).
- The vendor module blocklist is installed in both the first-stage ramdisk and `vendor_dlkm`. UFS
  access stays limited to the boot control service.

**API levels and VNDK.** The device's first API level is 35, and the board and vendor API level is
34. A newer framework does not move them. Do not copy a platform security patch value onto unchanged
vendor or boot input.

The DiamaneOS vendor partition has no VNDK version. Stock blobs link the current vendor variants of
their VNDK libraries, installed in the vendor partition, and reach LLNDK through the platform's
`/system/etc/llndk.libraries.txt`.

- Do not set `ro.vndk.version` or add a VNDK APEX. With `ro.vndk.version=34` the linker takes LLNDK
  from the VNDK 34 APEX, and the display HALs fail to link.
- The stock VNDK 34 library lists stay the review gate for which platform libraries a blob may use. A
  symbol a blob needs but the current library lacks shows as a named link failure on the phone.

**Choosing stock files.** Resolve dependencies by ABI and linker namespace, not by file name. Stock
holds AArch64 programs next to DSP firmware ELF files, and init declarations whose programs are
absent, so a declaration alone does not justify a service.

Look beyond the partitions' `lib64` directories: stock camera, graphics, sound trigger and audio
blobs also link libraries of the stock VNDK APEX, and one library comes from the tethering APEX. Do not
import the stock tethering package to satisfy a file name.

**Boot chain.**

- The FP6 bootloader loads `pvmfw` whenever the partition exists and refuses a slot whose AVB data
  omits it, even when unlocked. `pvmfw.img` goes to the same slot as the other boot images.
- The bootloader appends its own bootconfig keys, such as `androidboot.slot_suffix`. The kernel drops
  the whole bootconfig when a key appears twice, so never set those keys in `BOARD_BOOTCONFIG`.
- Every `first_stage_mount` entry needs its mount point in a verified image, because first-stage init
  cannot create directories on read-only partitions. A failed entry without `nofail` or `formattable`
  stops normal boot, while recovery still starts.
- DiamaneOS does not mount the stock `/odm/persist`, which exists only to import device-generated
  properties.
- When changing the ramdisk, unpack `init_boot` and check its first-stage `init`, snapuserd and build
  properties. A correctly sized and signed image can still hold an empty ramdisk.
