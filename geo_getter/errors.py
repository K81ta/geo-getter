from __future__ import annotations


MD5_VERIFIED = "md5_verified"
MD5_MISMATCH = "md5_mismatch"
MD5_UNAVAILABLE = "md5_unavailable"
SIZE_MISMATCH = "size_mismatch"
URL_UNAVAILABLE = "url_unavailable"
FASTQ_NOT_FOUND = "fastq_not_found"
UNSUPPORTED_ACCESSION = "unsupported_accession"
INSUFFICIENT_SPACE = "insufficient_space"
NETWORK_FAILED = "network_failed"
DOWNLOAD_COMPLETE = "download_complete"
INVALID_MANIFEST = "invalid_manifest"

ERROR_MESSAGES = {
    MD5_VERIFIED: "MD5 matched. No file corruption was detected.",
    MD5_MISMATCH: "MD5 did not match. The downloaded file may be incomplete, corrupted, or different from the expected file.",
    MD5_UNAVAILABLE: "ENA did not provide an expected MD5 value. The file was saved but could not be verified.",
    SIZE_MISMATCH: "The downloaded file size did not match the expected ENA size. The file was moved aside instead of being saved under the final name.",
    URL_UNAVAILABLE: "Could not retrieve FASTQ URLs from the public API. Check whether the accession is public and whether the GEO record links to SRA or BioProject.",
    FASTQ_NOT_FOUND: "No ENA direct FASTQ files were found. The record may contain only BAM/CRAM, ONT/PacBio native files, or GEO supplementary files.",
    UNSUPPORTED_ACCESSION: "The accession type is not supported.",
    INSUFFICIENT_SPACE: "The output folder does not have enough free space. Choose another folder or select fewer FASTQ files.",
    NETWORK_FAILED: "Network transfer failed. Check the connection and try again later.",
    INVALID_MANIFEST: "The FASTQ manifest is missing required columns or cannot be used for verification.",
}


class GeoGetterError(Exception):
    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail or ""
        super().__init__(self.user_message)

    @property
    def user_message(self) -> str:
        base = ERROR_MESSAGES.get(self.code, self.code)
        if self.detail:
            return f"{base}\nDetail: {self.detail}"
        return base
