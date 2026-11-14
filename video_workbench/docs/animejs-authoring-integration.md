# Anime.js authoring for the complete HyperFrames film

Anime.js 4.5.0 is an optional **animation authoring library**, alongside the existing GSAP capability. It does not replace HyperFrames or add a drawing engine: generated modules still declare HTML/SVG, Canvas or Three.js. Agent-authored composition remains free; the added helper owns no animation templates.

The official single-file ESM and MIT license are retained unchanged in `motion_renderer/vendor`. `animejs-assets.json` records official npm provenance, verified tarball integrity, exact file hashes and the separately authored adapter. Runtime does not fetch npm, CDNs, reference videos or external assets. The adapter disables Anime's default main loop and visibility resume, uses paused timelines, seeks from the real host's `global_frame` and rational `time`, and supports idempotent cleanup. It never controls the film clock or renders the final video itself.

## Opt-in release contract

Frozen `video.animation-runtime-capabilities.v1`, `video.motion-runtime.v3`, six image-owned film hosts and `video.animation-film-module.v1` remain unchanged. Capabilities **v2** adds the exact `authoring_libraries.animejs` descriptor and requires the complete, pinned set of ESM, license and clock adapter assets. Unknown files, partial libraries, changed hashes and an Anime drawing-engine name are rejected. The existing module contract already admits task-frozen asset names and hashes, so it needs no worker/profile source change.

`animejs_library.enable_in_capabilities(...)` verifies installed bytes and builds the operator-owned v2 table. It is a shape builder, not runtime admission. `FilmProductionSettings` verifies every installed asset against the v2 table; complete-film snapshots freeze the exact assets and image digest, and existing execution admission still binds the renderer image/runtime/hosts. `Dockerfile.film` copies and measures these assets for the next clean-source build. Merely copying them into an image does not enable the capability for a running service.

After a new complete-source build, an operator must freeze its digest, independently validate that exact renderer, issue its real execution-admission record and configure the whole-film release with the measured v2 capabilities and assets directory. Do not reuse an older image's admission or broaden the legacy experimental renderer's fixed resource list. This implementation does not build, deploy or enable a release automatically.

## Laboratory creator / reviewer / renderer path

The actual `method-author/review.v2` contract already accepts trusted extra constraint fields and an input asset hash map, so its frozen role/bundle need no alteration. `AnimationMethodService` now forwards the exact admitted `authoring_libraries` descriptor and self-contained authoring guidance into the immutable task constraints for planning, creation and review; retries retain their original frozen values. The guidance distinguishes the laboratory seconds-based seek interface from the formal module's real frame context. Browser requests cannot supply this server-owned configuration.

The method renderer has a separately opted-in `video.animation-method-render-request.v4` and receipt v4. It retains the existing complete HyperFrames project/bridge, then binds the exact library descriptor and three resources. Its trusted host serves `.mjs` correctly, checks the full resource set and records actual ESM/adapter script loads. Offering a library does not force its use: loading neither is valid; loading ESM without the paused adapter is rejected. A used-library observation requires both. All existing arbitrary/reverse/repeat/idle/fresh-load frame checks and isolated cleanup still apply.

For offline technical admission, instantiate `MethodAnimationRenderer(..., hyperframes=True, legacy_bridge=True, candidate_authoring_libraries=('animejs',))` and provide a task containing the pinned assets and `constraints.authoring_libraries`. Candidates remain absent from advertised capabilities. After an actual verified operation that loaded both scripts, write a separate operator record:

```json
{"schema":"video.animation-authoring-library-admission.v1",
 "runtime_identity":"the complete exact base renderer identity object",
 "authoring_libraries":"the complete exact descriptor object",
 "evidence":{"operation_id":"actual measured operation","evidence_sha256":"its verified evidence digest"}}
```

The two quoted object placeholders above must be replaced with the actual objects, never strings. Start the private executor with `--authoring-library-admission-file` and its existing `--method-project-runtime hyperframes-core-bridge`; its renderer reopens the stopped operation and verifies actual used-library evidence against the exact current image. Asset presence alone, a copied JSON descriptor, a failed sample or an operation that chose not to use Anime cannot admit the capability. The ordinary renderer image also packages the fixed assets for this explicit path. Legacy production contracts that hard-code earlier resource lists are deliberately not used for Anime; the formal continuation is the complete-film module path.

## Skill and developer release

`skills/animejs-frame-authoring/SKILL.md` contains local, reusable authoring knowledge and the actual host-context protocol, with no reference-video dependency. `animejs_library.foundation_skill_package()` builds an **unpublished foundation draft** for the existing developer review flow. It declares all eligible drawing engines and the exact authoring-library requirements. It is not inserted into the published catalog or assigned fabricated developer approval.

Create a complete effect segment with the relevant v2 task assets, validate deterministic seeking and native engine usage, then compare the entire sample against its expression goal. Transfer to another context before developer acceptance and ordinary Skill publication. Until that acceptance occurs, available code and a passing technical probe do not mean the effect library has a published, visually approved Anime.js effect.

## Validation

Node tests execute the real pinned ESM for forward, reverse, repeated and arbitrary seeks, object-state stagger, idle stability, exact host clock, lifecycle cleanup and rejected autonomous options. Python tests verify provenance, Skill packaging, strict v1/v2 separation and release rejection after asset tampering. A real-browser probe additionally exercises scoped SVG/DOM interpolation, path reveal, idle screenshot equality and the actual film realm guard; its result is technical compatibility evidence rather than a visual-quality or production-admission claim.

The browser probe accepts explicit `PANGHU_ANIME_PLAYWRIGHT` (installed `playwright-core/index.mjs`), `PANGHU_ANIME_BROWSER` (browser executable) and `PANGHU_TEST_TEMP` (scratch directory), then runs with `node --test tests/video_workbench/anime-timeline-browser.test.mjs`. It also renders actual Canvas and Three.js/WebGL2 with the corresponding native-context checks; it compares pixel hashes after nonmonotonic seeks, idle intervals and fresh reconstruction. Browser profile files stay in the supplied scratch directory. The test is explicitly skipped when these tools are unavailable.
