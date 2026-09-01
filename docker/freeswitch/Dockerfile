FROM safarov/freeswitch@sha256:b31c743f4c911a19687c61e3214968f2a24f93f9d3d667cc26284192e158ffc6

COPY conf/ /etc/freeswitch/

RUN mkdir -p /recordings /reference

EXPOSE 5060/udp 5060/tcp 8021/tcp

CMD ["freeswitch", "-nonat", "-nf", "-c"]
