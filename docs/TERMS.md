# Terms

Short explanations of the terms the other documents use.

- **AFU / BFU:**
  - After / before the first unlock since boot.
  - Before it, user data stays encrypted.
- **AVB:**
  - Android Verified Boot.
  - A custom AVB root is a project key enrolled instead of the maker's.
  - Such builds report **yellow**, maker-key builds **green**.
- **CE / DE storage:** readable only after unlock / from boot.
- **TEE:**
  - Qualcomm's TrustZone secure world.
  - **KeyMint** keeps keys there, **Gatekeeper** checks the lock credential and throttles guesses,
    **RKP** provisions attestation keys.
  - **StrongBox** and **Weaver** are secure-chip equivalents.
- **MTE:** CPU memory tagging that catches many memory-safety bugs.
- **HAL:** the vendor service between Android and a hardware driver.
- **QCRIL:**
  - Qualcomm's radio daemon.
  - **QMI/QRTR:** its messages and transport to the modem and DSPs.
- **IMS:**
  - Carrier voice (VoLTE), SMS and Wi-Fi calling over IP.
  - **IWLAN:** IMS over Wi-Fi.
  - **DCM** broker and daemon: the source-built service that brings up IMS data connections
    (Android-side app, vendor-side service).
- **eUICC / LPA:** the eSIM chip / the app that manages its profiles.
- **ISD-R:** the eUICC's management applet, which the LPA's card commands address.
- **SUPL, PSDS, XTRA:** GNSS assistance from a network server, predicted satellite data, Qualcomm's
  assistance service.
- **EDL:** Qualcomm's low-level flashing mode, needing a signed programmer.
- **pstore/ramoops:** kernel logs kept in RAM across a reboot.
- **SELinux:**
  - **enforcing** blocks what policy denies, **permissive** only logs it.
  - A **domain** is a process's label.
- **KMI / VINTF:** kernel module interface / framework-vendor compatibility level.
- **Gunyah / pKVM:** Qualcomm's hypervisor / Android's protected-VM hypervisor.
- **userdebug / user:** debuggable / production build.
- **n-day:** an attack on a publicly fixed bug.
- **Tally:**
  - DiamaneOS's interface.
  - The **Tally flag** turns its code on at build time.
