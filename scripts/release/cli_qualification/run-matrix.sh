#!/bin/bash
W=${QUAL_WORK:?set QUAL_WORK to the qualification work directory}; P=$W/venv-client/bin; E=$W/evidence
export HOME=$W/home TOKENPAK_HOME=$W/home/.tokenpak PYTHONPATH=$W/guard HTTP_PROXY= HTTPS_PROXY= NO_PROXY='*' PYTHONDONTWRITEBYTECODE=1
cd $W
L=$TOKENPAK_HOME/license.json; B=$TOKENPAK_HOME/license.bin-sentinel
[ -f $L ] || printf '{"synthetic": "sentinel-not-a-license", "signature": "UNSIGNED"}\n' > $L
[ -f $B ] || printf '\x00\x01\xffSYNTHETIC-BINARY-SENTINEL\x00' > $B
echo "before $(sha256sum < $L | cut -c1-64) $(sha256sum < $B | cut -c1-64)" >> $E/sentinels.txt
run(){ n=$1; shift; timeout 30 "$@" >$E/$n.out 2>$E/$n.err; rc=$?; echo "$n exit=$rc : $*" | tee -a $E/matrix.txt; [ $rc -eq 2 ] && echo "WARNING $n exit 2 = usage error, NOT a product outcome" | tee -a $E/matrix.txt; return 0; }
phase=$1
if [ "$phase" = A ]; then
 run A1-version $P/tokenpak --version
 run A2-license-json $P/tokenpak license --json
 run A3-activate-malformed $P/tokenpak activate SYNTHETIC-MALFORMED-TOKEN-0000
 run A4-plan $P/tokenpak plan
 run A5-doctor $P/tokenpak doctor
else
 run B1-license $P/tokenpak license --json
 run B2-version $P/tokenpak --version
 run B3-compress $P/tokenpak compress --help
 run B4-activate-short $P/tokenpak activate bad
fi
echo "after-$phase $(sha256sum < $L | cut -c1-64) $(sha256sum < $B | cut -c1-64)" >> $E/sentinels.txt
