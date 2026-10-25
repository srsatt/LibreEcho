# Fork OTA signer

`ota-signing-key.sops.yaml` contains the dedicated Ed25519 OTA seed, encrypted
only to the owner-approved `operator_recovery` recipient in `.sops.yaml`.
The corresponding public key is `ota-signing-key.public.hex`; its file SHA-256 is
`4cde1c7368824822578d5db61418da31257fa032fcdd069848e6176ce51fb151`.

Local signing uses the protected owner recovery identity through
`SOPS_AGE_KEY_FILE`. Decrypt only into a protected temporary signing input,
outside Git, and remove it when the build finishes. Never print the seed.
SOPS decryption and an Ed25519 signing roundtrip were verified in memory when
this signer was created; no plaintext key was written to disk.

Do not give the operator recovery age identity to CI: it decrypts unrelated
owner backups. If hosted signing is separately authorized, configure only this
dedicated OTA seed in the existing protected signing secret and validate its
derived public-key fingerprint. This encrypted copy remains the owner backup.

Creating this signer does not change either Echo's trust or firmware.
