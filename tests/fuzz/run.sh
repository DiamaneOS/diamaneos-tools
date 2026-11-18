#!/bin/bash
# Atheris fuzz targets of this folder. Linux x86_64 with Python 3.13 only, see the testing doc.
#   run.sh TARGET SECONDS WORKDIR   fuzz one target. Corpus, findings, throwaway keys and log stay in WORKDIR.
#   run.sh replay [WORKDIR]         run every input of regress.tar.gz once. Fails on a new kind of finding.
#   run.sh merge WORKDIR            write WORKDIR/regress.tar.gz from the archive and the corpora in WORKDIR.
# FUZZ_PYTHON is the Python with Atheris (default python3).
set -o errexit -o nounset -o pipefail
here=$(cd "$(dirname "$0")" && pwd)
python=${FUZZ_PYTHON:-python3}
# A merge starts each target again by its file name, which finds python3 on PATH.
if [ "${python#*/}" != "$python" ]; then
    PATH=$(cd "$(dirname "$python")" && pwd):$PATH
    export PATH
fi
archive=$here/regress.tar.gz
known=$here/known-findings.txt

targets() {
    for script in "$here"/fuzz_*.py; do
        script=${script##*/fuzz_}
        echo "${script%.py}"
    done
}

# Finding kinds without the source line, which moves with any edit.
kinds() {
    for txt in "$1"/*.txt; do
        [ -e "$txt" ] || continue
        head -n 1 "$txt" | sed -E 's/ at [^ ]+:[0-9]+$//'
    done | sort -u
}

fuzz() {
    local target=$1 seconds=$2 work=$3
    mkdir -p "$work/corpus" "$work/tmp"
    cd "$work"
    export FUZZ_FINDINGS=$work/findings FUZZ_KEYS=$work/keys TMPDIR=$work/tmp
    "$python" "$here/fuzz_$target.py" seeds corpus-seeds
    cp -n corpus-seeds/* corpus/ 2>/dev/null || true
    local start rc=0
    start=$(date +%s)
    "$python" "$here/fuzz_$target.py" corpus -max_total_time="$seconds" -rss_limit_mb=3072 -timeout=60 \
        -print_final_stats=1 > log.txt 2>&1 || rc=$?
    echo "target $target ran $(( $(date +%s) - start )) s, exit $rc" >> log.txt
    rm -rf "$work/tmp"
}

replay() {
    local work=${1:-} failed=0 target dir rc inputs new
    [ -n "$work" ] || work=$(mktemp -d)
    rm -rf "$work/regress" "$work/findings" "$work/tmp" "$work/logs"
    mkdir -p "$work/regress" "$work/logs"
    tar -xzf "$archive" -C "$work/regress"
    for target in $(targets); do
        dir=$work/regress/$target
        if [ ! -d "$dir/corpus" ]; then
            echo "$target: no inputs in the archive"
            failed=1
            continue
        fi
        inputs=("$dir/corpus")
        [ -d "$dir/pinned" ] && inputs+=("$dir/pinned")
        mkdir -p "$work/tmp/$target"
        rc=0
        FUZZ_FINDINGS=$work/findings/$target FUZZ_KEYS=$dir/keys TMPDIR=$work/tmp/$target \
            "$python" "$here/fuzz_$target.py" "${inputs[@]}" -runs=0 -timeout=120 -rss_limit_mb=3072 \
            > "$work/logs/$target.txt" 2>&1 || rc=$?
        new=$(kinds "$work/findings/$target" | while read -r kind; do
            grep -qxF "$target: $kind" "$known" || echo "$kind"
        done)
        echo "$target: $(find "${inputs[@]}" -type f | wc -l) inputs, exit $rc," \
            "$(kinds "$work/findings/$target" | wc -l) kinds of finding, $(grep -c . <<< "$new" || true) new"
        if [ "$rc" != 0 ] || [ -n "$new" ]; then
            [ -z "$new" ] || sed 's/^/  new: /' <<< "$new"
            echo "  log: $work/logs/$target.txt, findings: $work/findings/$target"
            failed=1
        fi
    done
    rm -rf "$work/tmp"
    if [ "$failed" != 0 ]; then
        echo "replay failed, see $work"
        return 1
    fi
    echo "replay passed"
}

merge() {
    local work=$1 target out old keys sources d file
    out=$work/merged old=$work/archive
    rm -rf "$out" "$old" "$work/tmp"
    mkdir -p "$out" "$old"
    [ ! -f "$archive" ] || tar -xzf "$archive" -C "$old"
    for target in $(targets); do
        sources=()
        for d in "$old/$target/corpus" "$work/$target/corpus" "$work/$target/corpus-seeds"; do
            [ ! -d "$d" ] || sources+=("$d")
        done
        [ ${#sources[@]} != 0 ] || continue
        keys=$work/$target/keys
        [ -d "$keys" ] || keys=$old/$target/keys
        mkdir -p "$out/$target/corpus" "$work/tmp/$target"
        FUZZ_FINDINGS=$work/tmp/$target/findings FUZZ_KEYS=$keys TMPDIR=$work/tmp/$target \
            "$python" "$here/fuzz_$target.py" -merge=1 "$out/$target/corpus" "${sources[@]}" \
            -timeout=120 -rss_limit_mb=3072 > "$work/$target-merge.txt" 2>&1
        if [ -z "$(ls "$out/$target/corpus")" ]; then
            echo "$target: the merge kept no input, see $work/$target-merge.txt"
            return 1
        fi
        # Pinned inputs are kept as they are: first inputs of past findings.
        for d in "$old/$target/pinned" "$work/$target/pinned"; do
            for file in "$d"/*; do
                [ -f "$file" ] || continue
                mkdir -p "$out/$target/pinned"
                cp "$file" "$out/$target/pinned/$(sha1sum "$file" | cut -c1-40)"
            done
        done
        # Only public keys and the list of signed messages. Private keys stay in WORKDIR.
        for file in "$keys"/*.pub "$keys"/*.pub.pem "$keys"/signed.json; do
            [ -f "$file" ] || continue
            mkdir -p "$out/$target/keys"
            cp "$file" "$out/$target/keys/"
        done
        echo "$target: $(ls "$out/$target/corpus" | wc -l) inputs, $(ls "$out/$target/pinned" 2>/dev/null | wc -l) pinned"
    done
    rm -rf "$work/tmp"
    tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@0 -C "$out" -cf - . | gzip -9n > "$work/regress.tar.gz"
    echo "$work/regress.tar.gz: $(wc -c < "$work/regress.tar.gz") bytes"
}

case ${1:-} in
    replay) replay "${2:-}" ;;
    merge) merge "$2" ;;
    *) fuzz "$1" "$2" "$3" ;;
esac
