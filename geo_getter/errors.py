from __future__ import annotations


MD5_VERIFIED = "md5_verified"
MD5_MISMATCH = "md5_mismatch"
MD5_UNAVAILABLE = "md5_unavailable"
SIZE_MISMATCH = "size_mismatch"
MISSING = "missing"
URL_UNAVAILABLE = "url_unavailable"
FASTQ_NOT_FOUND = "fastq_not_found"
UNSUPPORTED_ACCESSION = "unsupported_accession"
INSUFFICIENT_SPACE = "insufficient_space"
NETWORK_FAILED = "network_failed"
LOCAL_IO_FAILED = "local_io_failed"
DOWNLOAD_COMPLETE = "download_complete"
INVALID_MANIFEST = "invalid_manifest"
OUTPUT_PATH_INVALID = "output_path_invalid"
RESUME_REQUIRED = "resume_required"
RESUME_ARTIFACT_MISMATCH = "resume_artifact_mismatch"
RESUME_SUPPLEMENTARY_UNSUPPORTED = "resume_supplementary_unsupported"
UPDATE_NOT_AVAILABLE = "update_not_available"
UPDATE_ASSET_MISSING = "update_asset_missing"
UPDATE_ASSET_URL_MISSING = "update_asset_url_missing"
UPDATE_DIGEST_MISSING = "update_digest_missing"
UPDATE_DIGEST_INVALID = "update_digest_invalid"
UPDATE_DOWNLOAD_FAILED = "update_download_failed"
UPDATE_SHA256_MISMATCH = "update_sha256_mismatch"
UPDATE_VERSION_INVALID = "update_version_invalid"

ERROR_MESSAGES = {
    MD5_VERIFIED: "MD5 matched. No file corruption was detected.",
    MD5_MISMATCH: "MD5 did not match. The downloaded file may be incomplete, corrupted, or different from the expected file.",
    MD5_UNAVAILABLE: "ENA did not provide an expected MD5 value. The file was saved but could not be verified.",
    SIZE_MISMATCH: "The downloaded file size did not match the expected ENA size. The file was moved aside instead of being saved under the final name.",
    MISSING: "The file listed in the FASTQ manifest was not found.",
    URL_UNAVAILABLE: "Could not retrieve FASTQ URLs from the public API. Check whether the accession is public and whether the GEO record links to SRA or BioProject.",
    FASTQ_NOT_FOUND: "No ENA direct FASTQ files were found. The record may contain only BAM/CRAM, ONT/PacBio native files, or GEO supplementary files.",
    UNSUPPORTED_ACCESSION: "The accession type is not supported.",
    INSUFFICIENT_SPACE: "The output folder does not have enough free space. Choose another folder or select fewer FASTQ files.",
    NETWORK_FAILED: "Network transfer failed. Check the connection and try again later.",
    LOCAL_IO_FAILED: "Local file operation failed. Check the output folder permissions, disk state, and available space.",
    INVALID_MANIFEST: "The FASTQ manifest is missing required columns or cannot be used for verification.",
    OUTPUT_PATH_INVALID: "The output folder contains a path that cannot be used for a downloaded file.",
    RESUME_REQUIRED: "The output folder already contains files. Confirm resume before writing to this folder.",
    RESUME_ARTIFACT_MISMATCH: "The existing FASTQ manifest or download log does not match the current FASTQ selection.",
    RESUME_SUPPLEMENTARY_UNSUPPORTED: "Existing output folders can resume FASTQ downloads only. Choose an empty folder for GEO supplementary/processed files.",
    UPDATE_NOT_AVAILABLE: "No newer GEOGetter installer is available.",
    UPDATE_ASSET_MISSING: "The latest release does not include the GEOGetter installer asset.",
    UPDATE_ASSET_URL_MISSING: "The latest release installer asset does not include a download URL.",
    UPDATE_DIGEST_MISSING: "The latest release installer asset does not include a SHA256 digest.",
    UPDATE_DIGEST_INVALID: "The latest release installer asset has an invalid SHA256 digest.",
    UPDATE_DOWNLOAD_FAILED: "Could not download the GEOGetter update installer.",
    UPDATE_SHA256_MISMATCH: "The downloaded installer SHA256 did not match the GitHub release asset digest.",
    UPDATE_VERSION_INVALID: "The release version could not be parsed.",
}


class GeoGetterError(Exception):
    def __init__(self, code: str, detail: str | None = None, extra: dict[str, object] | None = None):
        self.code = code
        self.detail = detail or ""
        self.extra = dict(extra or {})
        super().__init__(self.user_message)

    @property
    def user_message(self) -> str:
        base = ERROR_MESSAGES.get(self.code, self.code)
        if self.detail:
            return f"{base}\nDetail: {self.detail}"
        return base
