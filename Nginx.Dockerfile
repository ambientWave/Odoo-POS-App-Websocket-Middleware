FROM alpine:latest

RUN apk add --no-cache nginx nginx-mod-http-headers-more && \
    ln -sf /dev/stdout /var/log/nginx/access.log && \
    ln -sf /dev/stderr /var/log/nginx/error.log && \
    mkdir -p /etc/nginx/modules && \
    ln -sf /usr/lib/nginx/modules/*.so /etc/nginx/modules/

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
