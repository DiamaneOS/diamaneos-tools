# Carrier configuration and Wi-Fi calling

How the FP6 build takes the stock carrier data and the Qualcomm IWLAN (Wi-Fi calling) parts from the
factory package, and how to check the result. For people who work on mobile network, IMS or Wi-Fi
calling behaviour. Terms are explained in [TERMS.md](TERMS.md).

Both come from the same authenticated factory release as the radio stack
([FP6-VENDOR.md](FP6-VENDOR.md)).

## Carrier configuration

`vendor product --aapt2 "$AAPT2"` extracts the XML assets and vendor defaults from the stock
`CustomerCarrierConfig.apk`. The APK is an input only, and nothing installs it.

The maintained CarrierConfig service uses the generated `fp6_stock_carrier_assets` filegroup through
its optional `diamaneos_carrierconfig.asset_module` setting. Android keeps its own carrier matching
by carrier ID, parent ID and MCC/MNC.

The extractor:

- keeps the asset bytes and their filter order
- gives carrier-ID files a fixed name, with the original name kept in the provenance
- skips old file names the stock service does not load
- repairs one known file, the malformed empty no-SIM XML, recorded by its exact hash
- refuses unsupported syntax, duplicate identities, external XML declarations and oversized files

The APK hash, the extractor, the `aapt2` program and the output hashes bind the result. The complete
stock APN list is installed as data, with its IMS and emergency entries, MVNO filters and priorities,
rather than merged.

**Corrections.** The stock `vendor.xml` also carries an unfiltered block that applies to every
carrier. The FP6 overlay of CarrierConfig's own `res/xml/vendor.xml` is read after it and is the
place for explicit corrections.

It turns off `world_phone_bool`, a Qualcomm default for CDMA world phones that would make Settings
show Qualcomm's network mode list instead of Android's, which carries GrapheneOS's LTE-only and
5G-only options.

**Known gap.** The stock data gives a few carriers Wi-Fi calling mode 10, a Qualcomm "IMS preferred"
value Android does not define. Android passes it to the IMS service unchanged, and Settings then shows
"Off" on the SIM page while Wi-Fi calling is on.

## IWLAN and certificate apps

The Qualcomm IWLAN app (`IWlanService.apk`) calls QCRIL's `IIWlan` service through
`libWlanServiceJni.so`. Its certificate helper gives CA certificates to the modem through
`libjnihelper.so`, `libcacertclient.so` and the CACert HIDL interface. `wlantunneld`
(`hardware/diamaneos/datad`) carries the modem's tunnel on the application processor's side.

The two apps are a trusted, modem-facing component:

- They keep the stock signer and share their own application UID, which does not isolate them from
  each other. Privileged placement keeps ordinary packages from joining that UID.
- They request only normal permissions, with no privileged grants. They have hidden-API access for
  their SystemProperties and HwBinder calls, with permission checks intact.
- Device SELinux confines the shared process to its radio Binder service and its certificate and QRTR
  paths.
- They are installed only for the system user and protected from being disabled by accident.

Review this exception with the app hashes on each stock update. Only one IWLAN implementation may be
selected, and AOSP's IWLAN service is not part of this configuration. CNE and AML (emergency location
messages) are not part of it either.

**Provisioning.** Carrier provisioning and the user's Wi-Fi calling choices stay authoritative.
Never replace authentication with forced provisioning flags. DiamaneOS's fork of Android's
entitlement module keeps carrier HTTPS activation and polling without Google push.

It stays inactive without carrier configuration, is not needed just to turn IMS or IWLAN on, and no
inspected FP6 carrier profile turns on its TS.43 activation.

## Checks

The host checks cover extraction, input closure and module placement. They do not cover the packaged
assets, VINTF, the shared UID, signatures and permissions, JNI linking or the enforcing policy.

After a build, compare the built `CarrierConfig.apk` with the vendor generation, from a checkout of
these tools:

```sh
PYTHONPATH=src python3 -m diamaneos_tools.carrier_data \
  --apk "$CARRIER_CONFIG_APK" --provenance "$VENDOR_PRODUCT/provenance.json"
```

This refuses missing, extra or changed device assets. It does not check the APK's signer, Android
permissions or how the modem behaves with a carrier.
