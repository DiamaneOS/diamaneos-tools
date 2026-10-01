# Carrier configuration and modem-backed Wi-Fi calling

For anyone working on FP6 telephony. The FP6 selection binds stock carrier data
and the necessary Qualcomm IWLAN inputs (IWLAN carries calls and messages to the
carrier over Wi-Fi) to the same authenticated factory release as the radio
stack. This is a private integration candidate, not evidence of carrier
compatibility.

## Carrier configuration assets

`vendor product --aapt2 "$AAPT2"` extracts XML assets and vendor defaults from
`CustomerCarrierConfig.apk`. The APK is an input only: no module installs it.

The generated `fp6_stock_carrier_assets` filegroup is used by the maintained
CarrierConfig service through its optional
`diamaneos_carrierconfig.asset_module` setting; other devices need not select
these FP6 assets. Carrier identity matching stays in Android, including specific
IDs, parent IDs and MCC/MNC (country/network code) fallback.

- The extractor keeps asset bytes and filter order. Carrier-ID filenames use a
  fixed suffix, with the original member name kept in provenance; display labels
  never enter the build system's shell commands.
- It leaves out legacy filenames the stock service does not load, and records
  one exact-hash repair for the malformed empty stock no-SIM XML. It rejects
  unsupported syntax, duplicate identities, external XML declarations and
  excessive input sizes.
- The APK hash, extraction implementation, aapt2 executable and output hashes
  bind the result.
- The complete stock APN (mobile data access point) XML is installed as data,
  keeping IMS/emergency rows, MVNO (virtual operator) filters and priority
  instead of merging overlapping APN definitions.

## Qualcomm IWLAN and certificate apps

The Qualcomm IWLAN app calls QCRIL's (Qualcomm radio interface layer's)
`IIWlan` service through `libWlanServiceJni.so`. Its certificate helper uses
`libjnihelper.so`, `libcacertclient.so` and the CACert HIDL interface to supply
CA material to the modem.

- Both apps keep the stock signer and share their own application UID.
  Privileged placement stops ordinary installed packages from joining that
  UID. Their permission declarations request only normal permissions, with no
  privileged grants.
- Device SELinux confines the shared process to the specific radio Binder
  service and the certificate/QRTR path. It is a trusted modem-facing
  component; the shared UID is not isolation between the two apps.
- The two packages have explicit hidden-API access for their SystemProperties
  and HwBinder calls; permission enforcement stays in force.
- They are installed only for the system user and protected from accidental
  disabling. Review this exception together with the app hashes on each update.
- CNE (Qualcomm's connectivity engine) is not selected.

Only one IWLAN implementation may be selected. The AOSP IWLAN fork is an
alternative candidate, not part of this FP6 configuration. Carrier provisioning
and the user's Wi-Fi calling preferences stay authoritative.

## Carrier entitlement

Carrier entitlement services need their own dependency and endpoint review; do
not substitute forced-provisioned flags for authentication. Android's inherited
entitlement module is replaced by the DiamaneOS source fork, which keeps carrier
HTTPS activation and polling without Google push dependencies. It stays
inactive without carrier configuration. It is not needed just to enable
IMS/IWLAN (IMS is the carrier's IP voice and messaging service), and no
inspected FP6 factory profile enables its TS.43 (GSMA entitlement) activation
flow.

## Testing

- Host checks cover extraction, input closure and module placement.
- The native build must check the packaged assets, VINTF, shared UID,
  signature/permission mapping, JNI linking and enforcing policy.
- Device tests must cover both SIMs, provisioning, voice/SMS, handover,
  suspend and VPN/lockdown interactions. A simulated emergency-network state is
  not evidence of emergency-call or location delivery. AML (Advanced Mobile
  Location) stays separate and unselected.

After the build, compare the actual `CarrierConfig.apk` with the authenticated
vendor generation, using its existing provenance rather than a newly invented
inventory:

```sh
PYTHONPATH=src python3 -m diamaneos_tools.carrier_data \
  --apk "$CARRIER_CONFIG_APK" --provenance "$VENDOR_PRODUCT/provenance.json"
```

This rejects missing, extra or changed device assets. It does not verify the
APK signer, Android permissions or native carrier behaviour.
