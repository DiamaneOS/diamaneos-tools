# Changelog

## Unreleased

- Install the stock touch controller firmware, and turn the stock LPA's eSIM
  service off again without its JNI library: it cannot list profiles on the
  FP6, and installed eSIM profiles keep working as SIMs.

- Build the FP6 kernel with the production configuration by default and check
  the vendor configuration too; keep unused modules out with a deny list that
  survives list regeneration; retire the NXP/ST NFC, ST eSE, mm-sys and Kleaf
  build forks.

- Pin the r9t kernel commits (no SELinux development mode, dmesg restricted,
  boot parameters handed to user space logged by name only) and a new fork of
  the SoC device tree that gives pstore/ramoops a memory region on the FP6.
  Keep debugfs as on r9s, pin the kprobe, firmware-fallback and debugfs
  settings that hardware support depends on, and correct the docs: the
  development profile only relaxes the configuration check.

- Move the interface design, branding and their bundled assets to the separate
  diamaneos-design repository.

- Bind explicit FP6 recovery setup and narrower UFS policy in a new product
  environment. Reject missing kernel verification tools before compilation.

- Reconstruct selected vendor files directly from pinned stock images; prepare,
  build and package the complete source kernel/module/DT set through public
  commands. Install generated inputs with inventory verification, preserve
  failed runs and reject edited sources or incompatible modules.

- Bind the native FP6 candidate to source-owned policy, explicit GPU firmware
  dependencies and a reduced optional performance-library selection. Document
  the pinned kernel rebuild and verified development image boundaries.

- Generate the selected FP6 native Android modules, service activation and
  configuration from authenticated stock inputs with atomic publication and
  explicit derived configuration provenance.

- Use GitHub as the authoritative source host and bind the updated FP6 overlay
  in a new candidate environment; retain independent Git backups.

- Authenticate an explicitly pinned additive source overlay while preserving
  upstream release verification and rejecting undeclared source inputs.

- Add selected stock-file generation with exact hashes, component-policy and
  dependency validation, retained image metadata and notices, and atomic
  publication. Reject unsafe paths and preserve prior output after failure.

- Verify source-built compatibility packages with explicit build provenance and
  retain their delivery type in package proofs. Parse bounded literal internal
  entities used by VTS while rejecting external and nested entities. Pin the
  derived ARM64 VTS package and its build/patch evidence.

- Reject undeclared source inputs between manifest projects, altered manifest
  exports and a manifest checkout at a different commit during build preflight.
- Document the Fairphone QSSI, API-level and kernel ABI boundaries for product
  integration without inheriting Pixel device defaults.

- Add authenticated, repeatable stock-image staging with bounded image selection,
  atomic publication and preservation of prior generations on extraction failure.

- Add a declared FP6 software-restart baseline with three monotonic-clock
  samples per repetition, separately retained Android boot milestones and an
  explicit manual-source-media boundary for physical cold power-on timing.
- Accept either the boot-animation exit property or the stopped boot-animation
  service as the recorded restart completion signal used by stock FP6 builds.
- Give the bounded declared connected report enough space for its full thermal
  observation series while retaining a strict report-size limit.
- Add an identity-bound switched-USB rig controller, persistent operation
  inhibitors, service-owned boot ADB, battery-policy scaffolding and automated
  verified-port idle disconnect/reconnect support.
- Add declared FP6 connected and camera measurement modes with exact protocol
  repetitions, series identity, staged original-media registration and explicit
  non-comparable outcomes.
- Add declared FP6 idle measurements with two series-bound eight-hour
  repetitions and an observed reconnect-before-capture flow.
- Add the target-bound staged device runner, initial read-only smoke suite,
  private role mapping, checkpointed evidence, explicit retry selection and
  destructive-stage interlocks.
