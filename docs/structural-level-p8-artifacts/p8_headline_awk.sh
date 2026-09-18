#!/bin/zsh
# P8 Step 3 — independent headline statistics via sed/awk only (no Python, no repo code).
# Joins candidates.jsonl (key, entry, stop, strategy, session) to outcomes.sealed.jsonl (key, result, pnl_ticks)
# and prints: terminal counts, WIN/LOSS, mean gross R and mean net R (pinned cost model), per instrument
# and per family.  Tick value: MNQ 0.5, MES 1.25; tick 0.25; slippage 2 ticks RT; commission $1.48 RT.
L=<checkout>/logs/structural_level_r5_2026_09_17/structural_level_r5
for INST in MNQ MES; do
  if [[ $INST == MNQ ]]; then TV=0.5; else TV=1.25; fi
  # candidates: key \t entry \t stop \t strategy \t session
  sed -E 's/.*"candidate_key": "([^"]+)".*"entry": ([-0-9.]+).*"session": "([^"]+)".*"stop": ([-0-9.]+).*"strategy": "([^"]+)".*/\1\t\2\t\4\t\5\t\3/' "$L/$INST/candidates.jsonl" > <scratch>/p8_tmp_cand_$INST.tsv
  # outcomes: key \t result \t pnl_ticks
  sed -E 's/.*"candidate_key": "([^"]+)".*"pnl_ticks": ([-0-9.]+|null).*"result": "([^"]+)".*/\1\t\3\t\2/' "$L/$INST/outcomes.sealed.jsonl" > <scratch>/p8_tmp_out_$INST.tsv
  awk -F'\t' -v TV=$TV -v INST=$INST '
    NR==FNR { entry[$1]=$2; stop[$1]=$3; fam[$1]=$4; sess[$1]=$5; ncand++; next }
    {
      key=$1; res=$2; pnl=$3; nout++;
      if (!(key in entry)) { unjoined++; next }
      cnt[res]++; fcnt[fam[key] "|" res]++;
      if (res=="WIN" || res=="LOSS") {
        st=(entry[key]-stop[key]); if (st<0) st=-st; st=st/0.25;
        gR=pnl/st; nR=(pnl*TV-2*TV-1.48)/(st*TV);
        n++; sg+=gR; sn+=nR; fn[fam[key]]++; fsn[fam[key]]+=nR;
        sn_s[sess[key]]+=nR; n_s[sess[key]]++;
      }
    }
    END {
      printf "== %s (awk path)\n", INST;
      printf "candidates=%d outcomes=%d unjoined=%d\n", ncand, nout, unjoined;
      printf "WIN=%d LOSS=%d NO_FILL=%d OPEN=%d terminal=%d\n", cnt["WIN"], cnt["LOSS"], cnt["NO_FILL"], cnt["OPEN"], cnt["WIN"]+cnt["LOSS"];
      printf "win_rate(terminal)=%.4f mean_gross_R=%.4f mean_net_R=%.4f sum_net_R=%.2f\n", cnt["WIN"]/(cnt["WIN"]+cnt["LOSS"]), sg/n, sn/n, sn;
      for (s in n_s) printf "  session %-9s n=%6d mean_net_R=%.4f\n", s, n_s[s], sn_s[s]/n_s[s];
      for (f in fn) printf "  family %-38s n=%6d WIN=%5d LOSS=%5d NO_FILL=%5d OPEN=%5d mean_net_R=%.4f\n", f, fn[f], fcnt[f "|WIN"], fcnt[f "|LOSS"], fcnt[f "|NO_FILL"], fcnt[f "|OPEN"], fsn[f]/fn[f];
    }' <scratch>/p8_tmp_cand_$INST.tsv <scratch>/p8_tmp_out_$INST.tsv
done
