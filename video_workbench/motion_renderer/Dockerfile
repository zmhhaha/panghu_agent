ARG NODE_BASE=node@sha256:b21fe589dfbe5cc39365d0544b9be3f1f33f55f3c86c87a76ff65a02f8f5848e
ARG CERTIFICATES_BASE=python@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f
FROM ${CERTIFICATES_BASE} AS certificates
FROM ${NODE_BASE}
# node:bookworm-slim has no system CA bundle. Bootstrap HTTPS from a pinned
# public trust bundle before asking apt to install its own ca-certificates.
COPY --from=certificates /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ARG DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
RUN sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=0 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update --error-on=any \
    && apt-get install -y --no-install-recommends \
       ca-certificates ffmpeg fontconfig fonts-noto-cjk python3 \
       libgbm1 libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 libxdamage1 \
       libxrandr2 libcups2 libasound2 libpangocairo-1.0-0 libxshmfence1 libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/renderer
COPY package.json package-lock.json ./
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browser \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1 CI=1 \
    PRODUCER_LOW_MEMORY_MODE=true PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=30000
RUN npm ci --omit=dev --no-audit --no-fund --fetch-retries=0 \
    && node node_modules/playwright-core/cli.js install chromium-headless-shell \
    && node -e "const fs=require('fs'),p=require('path');let found=[];const walk=d=>{for(const e of fs.readdirSync(d,{withFileTypes:true})){let f=p.join(d,e.name);if(e.isDirectory())walk(f);else if(e.isFile()&&['headless_shell','chrome-headless-shell'].includes(e.name))found.push(f)}};walk('/opt/browser');if(found.length!==1)throw Error('shell count '+found.length);fs.symlinkSync(found[0],'/opt/browser/headless_shell');" \
    && mkdir -p /opt/assets \
    && cp node_modules/gsap/dist/gsap.min.js /opt/assets/gsap.min.js \
    && cp /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc /opt/assets/NotoSansCJK-Regular.ttc \
    && npm cache clean --force \
    && groupadd --gid 10001 renderer \
    && useradd --uid 10001 --gid 10001 --home-dir /tmp/renderer --no-create-home --shell /usr/sbin/nologin renderer
ENV HYPERFRAMES_BROWSER_PATH=/opt/browser/headless_shell \
    PRODUCER_HEADLESS_SHELL_PATH=/opt/browser/headless_shell \
    PUPPETEER_EXECUTABLE_PATH=/opt/browser/headless_shell \
    XDG_CACHE_HOME=/tmp/cache XDG_CONFIG_HOME=/tmp/config TMPDIR=/tmp
COPY vendor/three.module.min.js vendor/three.LICENSE.txt vendor/three-assets.json /opt/assets/
RUN echo "08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7  /opt/assets/three.module.min.js" | sha256sum -c - \
    && echo "4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454  /opt/assets/three.LICENSE.txt" | sha256sum -c -
COPY vendor/anime.esm.min.js vendor/animejs.LICENSE.txt vendor/anime_timeline.mjs /opt/assets/
RUN echo "a19015a1a92d52025a2fb6703b6d67eadd1cc2aeaf880770e96e04cf6aa07be1  /opt/assets/anime.esm.min.js" | sha256sum -c - \
    && echo "3f3e835a9952cfc2a6ca836fb95b3257c981f24fa3ef51249055acb09931897b  /opt/assets/animejs.LICENSE.txt" | sha256sum -c - \
    && echo "b8d594eca930e0758be51e49724ca6274c1c1d3e95d16f43e78d8196fd89cf67  /opt/assets/anime_timeline.mjs" | sha256sum -c -
COPY runtime.mjs run.mjs observe.mjs creative.mjs contract.mjs engine_runtime.mjs hyperframes_project.mjs experiment.py ./
COPY hyperframes_film.mjs hyperframes_film_browser.mjs hyperframes_film_capture.mjs \
    hyperframes_film_root.mjs hyperframes_film_realm.mjs hyperframes_film_realm_guard.mjs \
    hyperframes_film_channel.mjs ./
RUN node runtime.mjs > /opt/renderer/runtime.json
ARG SOURCE_REVISION
LABEL org.opencontainers.image.source="https://github.com/zmhhaha/panghu_agent" \
      org.opencontainers.image.revision="${SOURCE_REVISION}"
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["node", "/opt/renderer/run.mjs"]
