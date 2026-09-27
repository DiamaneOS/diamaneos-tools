# Carrier configuration and modem-backed Wi-Fi calling

The FP6 selection binds stock carrier data and necessary Qualcomm IWLAN inputs
to the same authenticated factory release as the radio stack. This is a private
integration candidate, not evidence of carrier compatibility.

`vendor product --aapt2 "$AAPT2"` extracts XML assets and vendor defaults from
`CustomerCarrierConfig.apk`. The APK is an input only: no module installs it.
The generated `fp6_stock_carrier_assets` filegroup is consumed by the maintained
CarrierConfig service through its optional `diamaneos_carrierconfig.asset_module`
setting. Carrier identity matching remains in Android, including specific IDs,
parent IDs and MCC/MNC fallback. Other devices need not select these FP6 assets.

The extractor preserves asset bytes and filter order, excludes legacy filenames
that the stock service does not load, and records one exact-hash repair for the
stock malformed empty no-SIM XML. Unsupported syntax, duplicate identities,
external XML declarations and excessive input sizes are rejected. The APK hash,
extraction implementation, aapt2 executable and output hashes bind the result.
The complete stock APN XML is installed as data, preserving IMS/emergency rows,
MVNO filters and priority rather than merging overlapping APN definitions.

The Qualcomm IWLAN app calls QCRIL's `IIWlan` service through
`libWlanServiceJni.so`. Its certificate helper uses `libjnihelper.so`,
`libcacertclient.so` and the CACert HIDL interface to supply CA material to the
modem. Both apps retain the stock signer and share their own application UID.
Privileged placement prevents ordinary installed packages from joining that UID;
their permission declarations request only normal permissions, with no privileged
grants. Device SELinux confines the shared process to the specific radio Binder
service and certificate/QRTR path. It is a trusted modem-facing component; the
shared UID is not isolation between these two apps. CNE is not selected.
The two packages have explicit hidden-API access for their SystemProperties and
HwBinder calls; permission enforcement remains in force. They are installed only
for the system user and protected from accidental disabling. Review this exception
alongside the app hashes on each update.

Only one IWLAN implementation may be selected. The AOSP IWLAN fork is an
alternative candidate, not part of this FP6 configuration. Carrier provisioning
and user Wi-Fi-calling preferences remain authoritative. Carrier entitlement
services require their own dependency and endpoint review; do not substitute
forced-provisioned flags for authentication. Android's inherited entitlement
module is replaced by the DiamaneOS source fork, preserving carrier HTTPS
activation and polling without Google push dependencies. It remains inactive
without carrier configuration. It is not required merely to enable IMS/IWLAN,
and no inspected FP6 factory profile enables its TS.43 activation flow.

Host checks cover extraction, input closure and module placement. The native
build must check the packaged assets, VINTF, shared UID, signature/permission
mapping, JNI linking and enforcing policy. Device tests must cover both SIMs,
provisioning, voice/SMS, handover, suspend and VPN/lockdown interactions. Simulated
emergency-network state is not evidence of emergency-call or location delivery.
AML remains separate and unselected.

After the build, compare the actual `CarrierConfig.apk` to the authenticated
vendor generation (use its existing provenance, not a newly invented inventory):

```sh
PYTHONPATH=src python3 -m diamaneos_tools.carrier_data \
  --apk "$CARRIER_CONFIG_APK" --provenance "$VENDOR_PRODUCT/provenance.json"
```

This rejects missing, additional or changed device assets. It does not verify
the APK signer, Android permissions or native carrier behavior.
