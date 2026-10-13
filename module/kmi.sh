# SPDX-License-Identifier: GPL-2.0-only
# Match the real kernel release, not the Android OS version or spoofable uname.
# The packaged KMIs are listed in "$2/kmis" (one <android-os>-<kver> per line),
# so a new upstream KMI needs no change here.
#
# A release is selected only on the canonical GKI shape
#   <kver>.<numeric patch>[-<kmi generation>...][-<build id>...]
# i.e. a purely numeric patch level directly followed by the Android tag. The patch
# level is parsed out first (a glob "*" would happily swallow a "-vendor" token) so a
# reordered release like 6.6.118-vendor-android15-8, a malformed one like
# 6.6.118.android15-8, or a release with no Android tag at all is refused instead of
# guessed: the upstream loader may patch vermagic, so a wrong pick would look like a
# successful load rather than a clean failure.
select_kmi() {
  rel="$1"
  while IFS= read -r kmi; do
    [ -n "$kmi" ] || continue
    os="${kmi%-*}"
    kver="${kmi#*-}"
    case "$rel" in
      "$kver".*) ;;
      *) continue ;;
    esac
    # $rel is guaranteed to start with "<kver>." now, so these expansions strip it.
    patch="${rel#"$kver".}"
    tag="${patch#*-}"
    patch="${patch%%-*}"
    # The patch level must be digits and nothing else: 118 yes, 118-vendor no.
    case "$patch" in
      ''|*[!0-9]*) continue ;;
    esac
    case "$tag" in
      "$os" | "$os"-*) echo "$kmi"; return 0 ;;
    esac
  done < "$2/kmis"
  # Fallback for a custom kernel that keeps the GKI version but carries no Android tag in
  # osrelease (e.g. 6.1.176-Radioactive-V1.3-+): the strict match above finds nothing.
  # Only a release with NO Android tag at all may take this path. A tag that is present but
  # did not match the strict shape is a real mismatch (e.g. 6.6.118-android14-1-test on a 6.6
  # kernel, whose only packaged KMI is android15-6.6), and overriding it by version alone
  # would load a binary the release explicitly disowns.
  case "$rel" in *android[0-9]*) return 1 ;; esac
  # Match on the kernel version alone, keeping the numeric-patch guard, and only when that
  # version maps to a single packaged KMI: a KMI is ABI-stable across all of its patch
  # levels, so an unambiguous version cannot be a wrong pick, while a version shared by two
  # KMIs (5.10, 5.15) is refused rather than guessed. Accepted tradeoff: version-only
  # matching cannot tell a GKI 6.6 from a mainline 6.6, so a tag-less 6.6.50 maps to
  # android15-6.6.
  match=""
  n=0
  while IFS= read -r kmi; do
    [ -n "$kmi" ] || continue
    kver="${kmi#*-}"
    case "$rel" in
      "$kver".*) ;;
      *) continue ;;
    esac
    patch="${rel#"$kver".}"
    patch="${patch%%-*}"
    case "$patch" in
      ''|*[!0-9]*) continue ;;
    esac
    match="$kmi"
    n=$((n + 1))
  done < "$2/kmis"
  [ "$n" = 1 ] && { echo "$match"; return 0; }
  return 1
}
