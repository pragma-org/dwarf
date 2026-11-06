FROM dwarf/controller:current
LABEL io.dwarf.role=antithesis-workload
COPY test/ /opt/antithesis/test/
COPY package/ /opt/dwarf/package/
RUN chmod 0755 /opt/antithesis/test/v1/dwarf/*.py
ENV DWARF_ANTITHESIS_PACKAGE_DIR=/opt/dwarf/package
WORKDIR /home/dwarf/dwarf-fw
ENTRYPOINT ["python3", "dwarf/cardano-profile", "antithesis", "workload"]
