# Terms

Short explanations of the terms the other documents in this directory use, in alphabetical order.

| Term | Meaning |
| --- | --- |
| A/B, slot | Partitions kept twice, as `_a` and `_b`. An update writes the slot that is not running and switches to it. |
| ACK, GKI | Android Common Kernel, and the Generic Kernel Image built from it. Device drivers load as modules on top. |
| AVB | Android Verified Boot. `vbmeta` images hold the hashes and keys of the other partitions. A key of your own enrolled in `avb_custom_key` makes the phone report **yellow** instead of **green**. |
| EDL | Qualcomm's low-level flashing mode, which needs a programmer signed by the device maker. |
| Factory package | The device maker's archive of stock images and its flash script. |
| fastbootd | The fastboot mode of recovery. It writes the logical partitions inside `super`, which the bootloader cannot. |
| Gunyah / pKVM | Qualcomm's hypervisor / Android's protected virtual machine hypervisor. |
| HAL | The vendor service between Android and a hardware driver. |
| Image set | A directory of images the tools made, with its `build.json`, `resolved-manifest.xml`, `SHA256SUMS` and install scripts. |
| IMS, IWLAN | Carrier voice (VoLTE), SMS and Wi-Fi calling over IP. IWLAN is IMS over Wi-Fi. |
| KeyMint | The key store in the TEE. |
| KMI | Kernel Module Interface: the kernel symbols GKI modules may use. |
| otatools | `otatools.zip`, the host tools of an Android build that package, sign and check images. |
| pstore / ramoops | Kernel logs kept in RAM across a restart. |
| QCRIL, QMI, QRTR | Qualcomm's radio daemon, its message protocol, and the transport to the modem and signal processors. |
| RRO | Runtime resource overlay: a package that replaces another package's resources. |
| SELinux | Mandatory access control. **enforcing** blocks what policy denies, **permissive** only logs it. A **domain** is a process's label. |
| super, logical partitions | `super` holds the system, vendor and product partitions as logical partitions. |
| Tally | DiamaneOS's interface. |
| Target-files | `target-files.zip`, the build's archive of every partition and its metadata. Packaging, signing and updates start from it. |
| TEE | Qualcomm's TrustZone secure world. |
| user / userdebug | The production build / a debuggable build with root over adb, for development. |
| Vendor tree | The generated directory (`vendor/fairphone/FP6` for the FP6) that holds the stock files a build takes from the factory package. |
| VINTF | The compatibility contract between the Android framework and the vendor partition. |
| Workspace | The directory where the build tools keep images, logs, downloads and state. |
