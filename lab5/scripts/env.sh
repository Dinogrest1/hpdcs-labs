#!/usr/bin/env bash
# Опис стенду: машина, ОС, Go, навантаження.
source "$(dirname "$0")/lib.sh"
echo "# date: $(date '+%Y-%m-%d %H:%M:%S')"
echo "# chip: $(sysctl -n machdep.cpu.brand_string)"
echo "# логічних ядер: $(sysctl -n hw.logicalcpu), фізичних: $(sysctl -n hw.physicalcpu)"
echo "# перфоманс-ядер: $(sysctl -n hw.perflevel0.logicalcpu), енергоефективних: $(sysctl -n hw.perflevel1.logicalcpu)"
echo "# памʼять: $(( $(sysctl -n hw.memsize) / 1073741824 )) ГБ"
echo "# ОС: $(sw_vers -productName) $(sw_vers -productVersion)"
echo "# $("$GO" version)"
echo "# живлення: $(pmset -g batt | head -1)"
echo "# $(pmset -g | grep -i lowpowermode || echo 'lowpowermode: 0')"
load_line
echo "# найважчі процеси:"
ps -Ao pcpu,comm -r | head -4
