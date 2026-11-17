## Source-position speed ramps

`POST /retime-plans` accepts `project_id` and `curves`, one per trimmed timeline
occurrence. Each curve has two to sixteen `{"at_ms":0,"speed":1}` points,
starting at zero and ending at that occurrence's trimmed duration. Positions
are relative to the source trim; speeds range from 0.25 to 4 and vary linearly
with source position between knots. For example, a 400 ms occurrence can use
`[{"at_ms":0,"speed":0.5},{"at_ms":400,"speed":2}]`.
`GET /retime-plans/{id}` inspects the frozen plan, and
`POST /retime-plans/{id}/render` exports to a new `destination`.

The programme integrates the inverse speed over source distance, rather than
averaging endpoint speeds or treating the knots as programme positions. Audio
uses continuous fractional stereo sampling and changes pitch with speed;
there is no time-stretch or pitch-preservation claim. Each occurrence retains
its own trim, curve and gain, including repeated use of the same original.
A curve can alternatively be an object with `points` (the same knot list)
and `passes` from one to three. The first pass plays forward, the second plays
backwards over the same trimmed action and source-position speed curve, and
the third plays forward again. Every pass retains the occurrence gain. A
return pass revisits the same decoded source, rather than decoding a second
time. Pass boundaries preserve their fractional programme durations.
Video chooses the corresponding local source frame at each output frame
midpoint. Fractional durations carry across cuts; only the final programme is
padded to a 25 fps boundary, holding its last picture and padding audio with
silence. A plan supports one to eight occurrences and at most 30 seconds of
resulting programme. Saved plans remain unchanged by subsequent timeline edits
or a service restart; verified original relocation remains supported.

Each occurrence's video and audio is decoded once to temporary disk and
sampled with bounded reads. Source changes, missing originals, truncated stems
and existing output paths fail without overwriting destination files; private
temporary files are removed on failure. Proxy preview and sequential and
transition exports keep their separate behaviour.

## Bounded proxy retention

`POST /proxy-store/collect` accepts an existing `directory` and nonnegative
`max_bytes`. It removes the oldest retired service-owned proxy files until
that store reaches the requested byte budget, preserving current receipts and
files used by an in-flight preview. A refresh and offline preview can proceed
while collection runs; unrelated timeline edits do not wait for a preview to
finish. `remaining_bytes` reports tracked file bytes; `over_budget` is true
when protected or replaced files prevent reaching the requested budget.
Unregistered files are never removed. Earlier databases register their previous
accepted receipt when it is next refreshed. This is an explicit soft retention
budget, not an admission quota on encoders or free disk space.

Previews protect the exact selected proxy version through their final source
validation and publication. Protection is owned by the preview process and is
released on failure or process exit, including forced termination. A subsequent
collection can reclaim the retired version after reopening the service. The
current accepted version remains available while a new version is encoding or
if that refresh fails. Final exports still require the original source.
Collection does not repair externally replaced service files; those paths are
reported for operator inspection and preserved. Service store directories are
trusted: external writers must not rename files while collection is running.

# Media Workbench

## Saved overlapping transitions

`POST /transition-plans` accepts `{"project_id":1,"overlaps_ms":[80,80]}`
for a three-occurrence timeline. It freezes the current cuts and gains; inspect
it with `GET /transition-plans/{id}` and export with
`POST /transition-plans/{id}/render` and a new `destination` path.
The existing sequential render and voice-ducking routes remain separate.

Plans support one to eight occurrences, at least 40 ms each, and up to
30 seconds of resulting programme. Each overlap must be shorter than both
neighbors. An incoming and outgoing transition may overlap inside a short
middle shot: all active occurrences contribute simultaneously. Their linear
incoming/outgoing ramps are multiplied per occurrence, then normalized across
the active set. This prevents dark holes and preserves the middle shot.
RGB is composited in linear light, and original per-occurrence audio gains
are applied using the same coverage on the continuous 48 kHz clock.

Cut durations and overlap offsets retain millisecond precision. Video samples
coverage at each 25 fps frame midpoint. Only the final programme is padded to
a complete video frame; its last partial picture holds the last programme
instant, and the corresponding audio padding is silent. Reusing a source does
not combine its independently trimmed occurrences. Saved plans remain
independent of later edits and can resolve reconnected original media.

Decoders materialize each video and audio stream once on temporary disk. The
compositor reads one picture per active occurrence and fixed audio blocks,
keeping memory independent of programme length. Encoded output is published
without overwriting an existing path; failures remove private temporary files.

## Offline proxy editing and original reconnection

Video assets can have a 320×180 audition proxy. `POST /assets/{id}/proxy`
with `{"directory":"/existing/proxy-store"}` prepares and verifies a complete
proxy outside the database writer transaction. A new request reserves a new
generation; a late older request cannot replace the newer one. The last good
proxy remains usable during a refresh and after a failed refresh. Proxy files
are immutable and earlier accepted files are retained so in-flight previews
can finish; proxy-store retention is explicitly requested by the operator.

`POST /projects/{id}/preview` with `{"destination":"/new/preview.mp4"}`
renders the current timeline using verified proxies, falling back to verified
originals when a proxy is absent or damaged. This supports editing with the
original volume offline. A preview is an audition and does not become a
deliverable render job. Existing destination paths remain protected.

`GET /assets/{id}` exposes the original identity, current original path,
`link_revision`, and proxy generation/receipt. `POST /asset-relinks` accepts
`{"locations":[{"asset_id":1,"path":"/new/original.mp4","expected_revision":0}]}`.
Every selected file must contain the exact registered original bytes. All
members are inspected before a short atomic transaction; one wrong original,
stale location revision, or changed candidate rejects the whole batch. Media
probing and hashing leave other editing requests free to proceed. Reconnecting
the same original changes only its location revision, leaving clip edits,
asset identity and accepted proxy receipts intact.

Final MP4 and WAV delivery always uses the verified original, even when an
offline proxy is available. Frozen jobs and saved mix plans retain their
original trims, gains and content identities and can resolve an original's
accepted new location after reopening. Original absence blocks delivery;
a proxy is never evidence that the original has reconnected. Existing databases
gain additive tables and original asset rows need no manual migration.

A trusted-operator HTTP service for assembling sequential media into normalized MP4s. It supports ordered timeline edits, frozen exports, persistent render jobs, verified segment reuse, cancellation and recovery after a worker process disappears. Video is normalized to 640×360 at 25 fps; audio is stereo 48 kHz. Silent clips receive a bounded silent track. Each occurrence retains its own trim and gain even when the source is reused.

## Runtime

`POST /offline-bundles` creates a portable ZIP for an offline editing trip. Send
`projects` as one to sixteen unique `{project_id, priority}` objects (integer
priorities 1–1000), `max_media_bytes` as a nonnegative integer, and a new absolute
`destination`. Each candidate must have a nonempty timeline and a ready proxy
for every occurrence; unavailable candidates are reported and excluded. Original
volumes may be offline. The package includes whole project timelines, preserving
their intake revision, trims, ordering and gains, plus immutable selected proxy
bytes. Refreshing proxies or editing timelines during packing affects later
requests, never the request already captured. Database writes remain available
during optimization and copying, and collection respects the retained versions.

Capacity measures the sum of unique proxy byte lengths, excluding ZIP headers
and the manifest. The selection maximizes the sum of priorities of all fully
covered candidates, including those completed by another candidate's shared
media. Sharing is determined by proxy SHA-256, not occurrence count. Ties prefer
fewer media bytes, then lexicographically earlier sorted project IDs. A partial
project earns no value and is omitted. No fitting projects produces an empty
package. The archive contains `manifest.json` and `media/<sha256>.mp4` once per
unique rendition; clip paths are package-relative. The manifest records selected
snapshots, their priorities, value and actual media bytes. Selected files are
read once in bounded chunks; corruption aborts without publishing a target.
Existing destinations are protected and private temporary files are removed on
failure. The archive is independently usable after the catalogue or source
volumes disappear; it is an audition package, never an original-only delivery.

`POST /offline-bundle-sets` accepts at most twelve weighted `projects` and one
to three `volumes`, each with its own `max_media_bytes` and new `destination`.
Each ZIP represents one independent physical medium: an included project must
have all its media on that volume. A proxy shared within a volume costs bytes
once; the same proxy needed on two different volumes costs bytes on each.
Choose the whole set to maximize the total priority of distinct complete
projects, then minimize total media bytes across volumes, then prefer earlier
sorted project IDs, then the lexicographically earliest tuple of sorted media
hashes in input volume order. If several volumes cover a project, assign its
manifest to the earliest covering volume. Unused volumes contain an empty
manifest. Each manifest records its volume number/count and the set's value.
Selected source media are traversed once and streamed into every volume that
needs them. Frozen revisions and retained versions apply to the complete set;
corrupt input or failure to publish any member removes only this request's
already-published members. Existing files remain protected. Single-volume
bundles and ordinary proxy previews continue to use their original contracts.

Python 3.10+ on Linux or macOS, SQLite and FFmpeg/FFprobe with libx264/AAC support. The segment cache uses POSIX advisory locks. Set `MEDIA_FFMPEG` and `MEDIA_FFPROBE` to executable paths when needed. Start with `python3 -m mediaworkbench.server`; `MEDIA_PORT` (default 8765) and `MEDIA_DATABASE` (default `media-workbench.sqlite3`) configure the service. Keep it bound to loopback: there is no authentication, and the operator may read and write paths accessible to the process.

Existing databases are extended with job and checkpoint tables on opening. Original assets, projects, clips and render records remain readable. Old renders have no resumable job metadata and are available through the original inspection route.

## Timeline and synchronous export

Send JSON objects to these routes:

- `POST /assets`: `{"path":"/absolute/source.mp4"}` probes the media and records its content hash.
- `POST /projects`: `{"name":"Example"}` creates an empty timeline.
- `POST /clips`: `{"project_id":1,"asset_id":1,"start_ms":0,"end_ms":1000,"gain":0.5}` appends an occurrence and increments the revision.
- `POST /projects/1/timeline`: `{"expected_revision":1,"clips":[{"asset_id":1,"start_ms":1000,"end_ms":2000,"gain":1}]}` atomically replaces and reorders the whole timeline. A stale revision or invalid occurrence rejects the entire edit. Up to 512 occurrences are allowed per replacement.
- `GET /projects/1` returns one consistent timeline view.
- `POST /renders`: `{"project_id":1,"destination":"/absolute/new.mp4"}` executes a complete synchronous export.
- `GET /renders/1` returns the stored revision, frozen plan, status and output/error. New jobs also include lease and segment progress.

Clip endpoints use integer milliseconds. Gain is a finite number from 0 to 2. A source must remain unchanged after intake. Existing destinations, including dangling symlinks, are rejected. Rendering never replaces another writer's destination.

## Persistent jobs

`POST /render-jobs` accepts the same project and destination fields as `/renders` and returns immediately with a queued, immutable plan. Later timeline edits affect future exports only. A worker is explicitly driven with `POST /render-jobs/1/run` and an empty object `{}`. That request runs synchronously, while other HTTP threads may continue editing or inspecting the job. There is no automatic background scheduler.

`GET /render-jobs/1` inspects progress. `POST /render-jobs/1/cancel` with `{}` cancels a queued/running job and invalidates the claim; active FFmpeg processes stop when the worker next checks its lease. `POST /render-jobs/1/retry` with `{}` requeues a failed job using the same frozen plan; a separate `/run` request resumes it. Cancellation is terminal and completed exports cannot be cancelled.

A live claim excludes other workers. Claims expire after 30 seconds without renewal; after a process disappears, another `/run` request may take over the expired claim. Each acquisition increments an epoch. Heartbeats, checkpoints, failure updates and final publication all require the current, unexpired epoch and owner. Source hashing, FFmpeg execution and cache-lock waits renew the lease without holding a long SQLite write transaction.

The default cache is `.media-workbench-cache` next to the database. A segment identity includes source content, trim, gain, audio presence and the encoding profile version. Shared segments have process locks, atomic metadata replacement and byte checksums. Every cache hit and resumed checkpoint is revalidated; corrupt or missing segments are rebuilt. Repeated occurrences may reuse the same encoded bytes but keep separate positions in the plan and concatenation list. Final assemblies retain unique hidden directories beside the destination so an interrupted publication can be recognized by inode ownership and resumed. A competing destination is preserved. Public output paths are never automatically unlinked, because another process may replace the name between an ownership check and deletion. If a process already linked its artifact before a failure or cancellation, those bytes are retained and the job exposes its artifact intent. An explicit retry may adopt a valid own link; cancellation never marks that file completed. A corrupt published artifact requires operator inspection/removal before retry, and another writer's file is never replaced.

Retain the cache alongside the database while jobs may need recovery. There is no cache eviction API, automatic retry loop or power-loss durability guarantee across filesystem and database. Recovery covers worker process interruption; it does not cover storage hardware failure or malicious mutation of database/cache files. Completed MP4s remain valid if the cache is later removed.

## Modules and validation

`timeline` owns atomic edits and revisions; `planner` owns frozen occurrence identities; `jobs` owns durable claims and checkpoints; `segment_cache` owns encoded bytes and manifests; `worker` coordinates recovery; `publication` joins database ownership with destination ownership. `encoder`, `assets`, `tools` and `server` implement media processing, intake, bounded subprocesses and HTTP access.

Automated test files remain deferred to the later task-verification stage. Bounded executable workflow checks are provided in `mediaworkbench.check`:

```sh
python3 -m mediaworkbench.check
python3 -m mediaworkbench.check --workflow timeline
python3 -m mediaworkbench.check --workflow render
python3 -m mediaworkbench.check --workflow recovery
```

They generate temporary media and databases, exercise real production operations and inspect the resulting MP4 with FFprobe. Independent concurrency, corruption and process-interruption verification is kept outside the delivered baseline.

## Voice ducking and saved audio mixes

The audio mix workflow exports a separate stereo 48 kHz, 16-bit WAV from the
project's sequential background timeline and up to 16 positioned voice
occurrences. It does not change the MP4 render-job workflow. Programmes are
limited to 600 seconds and 512 detected audible spans across the voice layer. Background occurrences may be audio-only sources;
MP4 exports retain their existing requirement for video.

- `POST /projects/1/voices`: `{"expected_revision":2,"voices":[{"asset_id":3,"start_ms":200,"end_ms":1800,"at_ms":500,"gain":0.7,"duck_db":-12}]}`
  atomically replaces the voice occurrences and increments the shared project
  revision. Voice sources must contain audio, and edits reject stale revisions.
- `GET /projects/1/audio` returns one consistent background and voice view.
- `POST /mix-plans`: `{"project_id":1,"threshold_db":-38,"attack_ms":40,"release_ms":160}`
  freezes a saved audition plan. Defaults are shown. A 20 ms stereo RMS window
  after occurrence gain identifies audible intervals; silence, muted voices,
  trims and short final windows retain their actual meaning.
- `GET /mix-plans/1` returns the immutable plan, audible spans and piecewise
  linear background-gain envelope. Times in these arrays are 48 kHz sample
  positions. The stored payload checksum is checked on reading.
- `POST /mix-plans/1/render`: `{"destination":"/absolute/new.wav"}` exports
  that saved plan and reports its revision, checksum, peak and whole-programme
  output scale. Later edits do not affect it; changed source bytes are refused.

Each audible span contributes a linear attack, constant duck gain and linear
release. Overlap uses the strongest attenuation, including intersections
between ramps; it never multiplies duck gains. The foreground remains at its
specified relative gains. Mixing sums all active foreground occurrences with
the attenuated background before measuring peak. One scale of at most 1 keeps
the complete programme below 0.98 sample peak, preserving relative loudness
between early and late material without independently normalizing blocks.

Render decodes each occurrence once to private temporary PCM, reads fixed-size
blocks twice (peak scan and output), and deletes temporary files on success or
failure. Its working memory does not grow with programme length; temporary
storage scales with decoded duration and is required. A 300-second programme
is independently checked in an OrbStack container capped at 192 MiB. This is
sample-peak protection, not a broadcast loudness or intersample-peak limiter.
Existing output names and symlinks are preserved. No live audio playback,
background mixing scheduler or network source reader is provided.

## 协作编辑与选择性撤销

`GET /projects/{id}/edits` 返回含修订和剪入谱系的编辑视图。
`POST /projects/{id}/edits` 接收 `actor`、`request_id`、`base_revision` 和 `actions`。
操作包含：`delete`（`start_ms`、`end_ms`）、`move`（同上及原视图坐标的 `to_ms`）、
`insert`（`at_ms`、`clips`，片段属性与普通时间线一致）。一批内后续操作的坐标基于
前序操作完成后的视图；每批最多32操作，当前时间线最多512次剪入。

服务将旧视图中的选择解析为具体剪入的源区间，再合并到当前状态。互不相交的删除或插入
可合并；目标内容被删去、替换或移走时整批冲突，不自动选择“相似”素材。
跨拆分保留剪入身份，重复素材的每次剪入彼此独立。插入间隙的两个邻点均保留时，
插入在右邻点之前；仅剩一个邻点时跟随该邻点；两者均消失时拒绝。
整份旧式时间线替换会开启新的谱系，旧协作视图应重新打开。

相同项目、操作者和请求标识的相同请求返回原始提交回执，不重复编辑，也不返回后来版本；
同一标识换内容则拒绝。撤销请求使用相同入口和 `actor`、新 `request_id`、`undo_request`，
只撤回该操作者先前那批操作。撤销保留无关的后来编辑；依赖内容已改变则整批拒绝。
历史采用内容寻址的持久序列树，共享未变化子树，回执仅保存修订引用；不为每次短编辑
复制512次剪入的完整历史。设计容量为512剪入、一天一万批短编辑的协作历史不超过64 MiB；
同日旧版本合并、回执重放和未冲突撤销可用。当前未自动过期历史。
数据库提交仅覆盖短编辑事务，媒体任务仍冻结已提交快照后在事务外执行。


## Calibrated multicamera editing

`POST /multicam-plans` saves an independent camera programme. Fields are
`project_id`, `master_asset_id`, `duration_ms`, `cameras`, `minimum_shot_ms`
(default 400), `max_cuts` (default 8), and `cut_cost` (default 0).
A camera occurrence contains `asset_id`, `anchors` with `programme_ms` and
`source_ms`, and ordered disjoint `quality` intervals (`start_ms`, `end_ms`,
`score`). Repeated camera assets remain independent occurrences.

Two to sixty-four increasing clock observations define an exact piecewise
linear programme-to-source mapping. Extrapolation is not permitted. Scores
are integers from -1000 to 1000 on the programme clock; gaps have score zero.
The programme is one to 750 complete 25 fps frames. At most four cameras and
sixteen cuts are supported. Each selected camera must cover its entire output
frame. The minimum shot duration rounds up to frames and applies to the first
and last shots too. Select the complete feasible camera sequence maximizing
the sum of frame-midpoint scores minus `cut_cost` per switch; ties select the
lexicographically first sequence of input camera indexes. No feasible full
sequence means no plan is saved.

`GET /multicam-plans/{id}` reads the checksummed frozen result, and
`POST /multicam-plans/{id}/render` with `destination` exports it. Pictures use
the calibrated source time at each programme frame midpoint, including after
camera changes. The continuous master audio is independent of camera cuts.
Later timeline edits do not affect saved camera plans. Original reconnection
remains supported; originals must match their saved content identities.
Rendering uses one disk materialization per camera occurrence and bounded
frame memory, refuses existing destinations and cleans temporary work after
failure. These are operator-selected quality curves, not automatic visual
quality detection or audio-based clock inference.

## Listener-oriented first-order soundfields

`POST /spatial-plans` freezes a trimmed four-channel recording and listener
keyframes. Supply `project_id`, `asset_id`, integer `start_ms`/`end_ms`,
`convention` (`ambix` or `fuma`), and `keyframes` containing `at_ms` and `q`.
The trim can span 1 ms to 30 seconds. Keyframes cover its complete duration,
with 2–64 strictly increasing times relative to the trimmed programme.
Quaternions are normalized `[w,x,y,z]` listener-to-world poses in right-handed
X-front, Y-left, Z-up coordinates. Equivalent signed quaternions describe the
same orientation. Adjacent poses take the shortest spherical path at constant
angular speed; a dot-zero half-turn uses canonical endpoint signs (first nonzero
quaternion component positive), so equivalent input signs give the same path.
Dot magnitudes at most 1e-15 count as a half-turn roundoff tie.

AmbiX input is ACN/SN3D `[W,Y,Z,X]`; FuMa input is maxN `[W,X,Y,Z]`, with its
W coefficient scaled by sqrt(2) on conversion to SN3D. Directional coefficients
are expressed in listener coordinates using the inverse listener pose. Rotation
preserves omnidirectional pressure and the length of the directional vector;
there is no loudness compensation, clipping or stereo downmix. Four channels
are an explicit soundfield convention supplied by the operator, not inferred
from a WAV speaker-layout label.

`GET /spatial-plans/{id}` reads the checksummed frozen plan. Export with
`POST /spatial-plans/{id}/render` and a new `destination`; output is 48 kHz,
four-channel float WAV in ACN/SN3D order. The programme clock begins at the
trim start and continues across all blocks. Samples are independent of block
partition. Original media are decoded once to private disk, transformed with
bounded buffers, verified again and published without replacing an existing
path. Saved plans survive reopening and resolve accepted original relinks.
Ordinary stereo workflows remain separate.

`POST /spatial-plans/{id}/audition` exports a short listening excerpt with
`destination`, integer `at_ms` and `duration_ms` relative to the saved programme.
Auditioning a later interval uses that interval's original listener poses,
without restarting the motion. Its samples match the corresponding slice of
full delivery, so the editor can inspect any passage before handing over the
unchanged frozen plan. Excerpts also retain all four soundfield channels.

A delivery device can impose `max_poses` (2–64, default 64). When the recorded
trajectory exceeds this capacity, the frozen plan selects a subset of the
original poses including both endpoints. It minimizes the greatest angular
orientation error at all original observation times, measured against shortest
spherical interpolation between retained poses. The error is a rotation angle,
independent of quaternion sign. `original_poses`, retained `poses`, and
`max_error_deg` are saved together. Errors within 1e-9 degrees are tied. Equal error chooses the lexicographically
first tuple of retained input indices (across all allowed node counts).
Both audition and full delivery consume the retained frozen trajectory, so
an editor hears exactly what the capacity-limited device will receive.

## Source transcripts and bounded caption delivery

`GET/POST /assets/{id}/transcript` reads/replaces an original's transcript.
POST takes `expected_revision` (initially zero) and `words`, up to 128 ordered,
nonoverlapping `{start_ms,end_ms,text}` atoms within that original. An atom is
an indivisible printed word/phrase without whitespace or control characters;
Unicode text is NFC-normalized. Chinese phrases can be separate atoms without
requiring a language tokenizer. Text is literal, including `<` and `&`.
A stale replacement fails atomically. This workflow only edits transcripts;
it neither reads media bytes nor changes the ordinary timeline revision.

`POST /caption-plans` accepts `project_id`, `width` (4–80 display cells,
default 32), `max_cues` (1–128, default 32), `minimum_ms` (default 400),
`maximum_ms` (default 5000, up to 10000), and `hold_ms` (0–2000, default 500).
The plan captures one consistent timeline/transcript view. Original volumes
may be offline. Every *fully contained* word in every trimmed occurrence is
projected from source milliseconds to programme milliseconds; incomplete words
cut by either trim endpoint are omitted. Repeated uses of an original remain
separate. A plan supports ten minutes and at most 128 projected words. Clips
without transcripts remain silent gaps in the caption programme.

Every projected word must appear exactly once in order. A cue contains up to
32 successive atoms from one occurrence, on at most two lines. Atoms never
split. One space separates atoms on each line. Combining marks occupy zero
cells, Unicode East Asian W/F characters occupy two, other characters one.
This is a portable display-cell contract, not a claim of proportional-font
pixel measurement or automatic language-aware typesetting.

Each cue starts with its first spoken word and ends at the earliest of its
last word's end plus `hold_ms`, the next projected word's start, its own clip's
end, and its first word's start plus `maximum_ms`. It must fully cover its last
word and last at least `minimum_ms`. A cue may use a silent gap for reading but
never crosses its clip boundary or covers a later word. These are exact integer
milliseconds. Empty projected transcripts produce an empty valid caption plan.
An impossible complete layout fails without saving a partial plan.

The whole programme minimizes its worst cue reading rate (display cells of
word text per second, excluding joining spaces) subject to the cue-count cap.
Ties minimize total squared unused cells across the chosen nonempty lines,
then cue count, then the tuple of cue-ending word indexes; line-layout ties
prefer the earliest line split. It does not optimize each clip independently
or merely average reading rates. Exact rational rates are returned as
`worst_rate: [numerator,denominator]` along with `raggedness`, projected tokens
and final cues. Optimization runs outside the database writer transaction.

`GET /caption-plans/{id}` reads the saved, checksummed plan.
`POST /caption-plans/{id}/preview` with `at_ms` returns its active cues on
half-open time intervals. `POST /caption-plans/{id}/export` with a new
`destination` writes portable UTF-8 WebVTT using those same cues, millisecond
timestamps and escaped literal text. No original media, catalogue lookup or
subtitle regeneration is required by a caption player. Saved plans retain
original timeline/transcript revisions across later editing and service restart;
new plans use the new versions. Failed exports remove private temporary files,
and existing targets including dangling symlinks remain untouched. This caption
workflow neither burns text into video nor changes ordinary media exports.

## Spectral restoration

`POST /noise-profiles` accepts `{asset_id,start_ms,end_ms}` and measures a
stationary noise-only interval of 100 ms–10 s from a verified original. Profiles
are immutable and portable across recordings made with the same noise setup.
The power spectrum uses the same 512-sample sine analysis window as restoration,
with a 128-sample hop, at stereo 48 kHz. Channel power is averaged over complete
windows; padding never contributes to the measurement.

`POST /denoise-plans` accepts `{project_id,profile_id,strength,floor_db,release_ms}`.
Defaults are 1, -24 dB and 80 ms. Strength ranges 0–4, the amplitude floor -60–0 dB,
and release 1–1000 ms. A plan freezes the complete timeline, gains, measured noise
and settings; later edits and newly sampled profiles affect only new plans.
`GET /denoise-plans/{id}` retrieves that frozen version with checksum validation.

`POST /denoise-plans/{id}/render` accepts `{destination}` and writes stereo PCM16
WAV. The programme must be nonempty and at most 30 s. A joint-channel power ratio
sets one real, conjugate-symmetric attenuation per bin. The amplitude floor
preserves residual texture; an instantaneous opening protects new transients,
while exponential closing avoids abrupt spectral holes. No independent channel
normalization, phase replacement or output loudness compensation is performed.
Strength zero is transparent, including the first and last samples. Analysis and
synthesis windows are normalized by their actual accumulated squared weights.
Clip gain applies after noise estimation/restoration, preserving the calibration
and quiet repeated occurrences. The filter clock and history span the programme,
including cuts; each occurrence contributes its own raw original samples.

`POST /denoise-plans/{id}/audition` additionally accepts `{at_ms,duration_ms}`.
It delivers exactly the corresponding sample slice of the full WAV, warming the
causal filter from programme start, with the same frozen plan. `block_frames`
(1–65536, default 4096) controls I/O only, never the analysis grid or gain history.
Originals are decoded once per occurrence per request into a private disk spool;
only bounded FFT and I/O buffers stay in memory. Processing does not hold a
SQLite write transaction. Accepted original relinks work without changing the
plan; missing or changed originals fail, and proxies cannot replace originals.
A failed request removes temporary work, and delivery never replaces an existing
file. Ordinary stereo mixing and its peak compensation remain separate.

## Measured stereo room responses

`POST /room-plans` freezes a timeline and one to three measured room stages.
Each stage provides `asset_id`, `at_ms` and `fade_ms`; the first starts at zero
without a fade and later fades must not overlap. The response is a four-channel
48 kHz recording, up to one second, in output/input order LL, LR, RL, RR.
Its signed amplitudes, interchannel calibration, delays and full decay are
retained. Response bytes are frozen in the plan, so later relocation, removal
or catalogue changes to the response never change a saved acoustic space.

Room changes interpolate the excitation sent to each room, preserving earlier
reflections already in flight. They do not fade away a previous room's tail.
Per-occurrence gains precede the room; wet gain follows the full stereo matrix
and dry gain is added without limiting or implicit normalization. The default
is wet-only. Programmes support eight occurrences, up to thirty seconds.

`GET /room-plans/{id}` inspects the saved plan. `POST /room-plans/{id}/render`
and `/audition` write float stereo WAV to a new `destination`. Optional
`at_sample` and `samples` select an exact slice of the full programme plus
the longest response minus one sample. `block_frames` changes only output I/O.
Each occurrence decodes once to disk. Partitioned convolution keeps memory
bounded by the measured responses rather than programme length; partial
auditions include prior excitation, and the final decay is fully flushed.
Original reconnection, source validation, destination protection and private
file cleanup remain active. No GPU or third-party Python packages are required.


## Target-duration music assembly

`POST /projects/{id}/music` replaces a revisioned phrase catalogue with
`sections`, `joins` and `expected_revision` (zero initially). Each phrase has
unique `name`, `asset_id`, `start_ms`, `end_ms`, optional `gain` (0–2), integer
`score` (−10000–10000), and `max_uses` (1–3). Eight phrases and one-minute
source recordings are supported. Directed joins have `from`, `to`, `cost`;
a missing join is forbidden. Source times belong to the recording, not the
assembled programme. Section order resolves equal-quality ties.

`POST /music-plans` accepts `project_id`, exact `target_ms` (up to 30000),
`overlap_ms` (0–2000), and allowed `starts` and `ends` names. The uniform
overlap must not exceed half any phrase. A complete route of at most twelve
uses maximizes summed phrase scores minus join costs, respecting all use
limits and openings/closings. Equal scores prefer fewer uses, then the
lexicographically earliest catalogue-index sequence. No stretching, trimming,
silent padding or partial plans are allowed; infeasible requests fail before
persistence. Both the catalogue and chosen schedule are frozen in the plan.

`GET /music-plans/{id}` inspects the result. `/render` and `/audition` accept
`destination`, optional `at_sample`, `samples`, `block_frames` (1–16384) and
write float stereo 48 kHz WAV. Each interior join uses cosine outgoing and
sine incoming equal-power weights over the exact overlap, preserving each
phrase's source cut, channels and gain without normalization or limiting.
Auditions are exact slices of the full output even inside joins. Each distinct
selected source decodes once to disk; repeated uses seek within that spool.
Memory stays bounded by the output block, source relocation remains supported,
changed originals are refused, existing destinations are protected, and private
spools disappear on failure. Catalogue or timeline edits do not change saved plans.

## Broadcast programme mastering

Install the Python package with `python3 -m pip install -e .` (NumPy and SciPy),
plus FFmpeg/FFprobe. `POST /master-plans` freezes a previously saved `mix_id`,
`target_lufs` (default -23, range -30 to -10) and `ceiling_db` (default -1,
range -6 to -0.5). It supports stereo 48 kHz programmes from 400 ms to 30 s.
The embedded mix, occurrence trims, gains, ducking trajectory and editorial
revision remain immutable; later changes to the original mix do not participate.
`GET /master-plans/{id}` inspects that version. `/render` and `/audition`
accept `destination`, `at_sample`, `samples` and `block_frames` (1–16384).
Audition indices are exact 48 kHz programme samples. They crop the completed
master, including gain recovery that started before the excerpt.

The loudness meter implements two-stage 48 kHz K weighting, 400 ms stereo
energy windows on a 100 ms hop, a -70 LUFS absolute gate and a relative gate
10 LU below the admitted mean. This differs from the ordinary mix workflow's
sample-peak compensation. True peaks are evaluated after 4x, 64-tap FFmpeg
resampling, including both channels. A shared stereo gain trajectory guards
neighbouring 10 ms cells and limits gain changes to 0.25 dB per cell; global
input gain is calibrated against the measured, limited complete programme.
A 0.15 LU delivery tolerance and final reconstructed-peak check are mandatory.
A silent or infeasible programme fails before publishing. The delivered WAV is
stereo 48 kHz float PCM; metrics describe the complete master even for excerpts.
This is a bounded programme mastering processor, not a promise of a specific
commercial limiter's envelope or an unrestricted realtime streaming processor.

Each original occurrence is decoded once into private temporary storage. Source
PCM, weighted PCM and candidate masters live on disk; only bounded processing
blocks and scalar analysis cells live in memory. Block size does not reset filter,
gain or programme time. Source identities are rechecked after processing and
current original locations are resolved through the existing relink mechanism.
Publication refuses existing targets, and failed work releases private files.
Ordinary mix, room, denoise and music deliveries retain their own processing.


## HDR 素材的 SDR 颜色交付

`POST /color-plans` 接收 project_id、每次剪入的 encodings（pq2020、linear2020、srgb709）及 white_nits（80–300）。当前颜色交付为160×90、25fps无声无损FFV1/MKV审片文件，入出点须在40ms帧网格上，与普通剪辑成片相互独立。`GET /color-plans/{id}` 重开冻结方案，`POST /color-plans/{id}/render|preview` 接收 destination、可选 at_frame/frame_count。最长30秒，每个剪入的全镜头统计只决定该实例曝光；中段预览不会重新分析局部曝光。PQ按绝对亮度解码并由BT.2020变换到BT.709线性光，保留中间调、连续高光肩部和亮度/色相一致的色域压缩，最后用sRGB显示编码。中间调以去除两端各5%的对数亮度统计定位18%显示白，小面积极亮物不应支配主体。高精度中间数据存临时磁盘，每次剪入原片只解码一次，逐帧分析和输出不保存整片画面；失败释放临时目录，源变化拒绝交付，目标禁止覆盖，支持原片回连。

颜色审片支持最高1280×720输入，linear2020代码值1.0表示1000 nits，srgb709代码值1.0表示100 nits，pq2020采用ST 2084绝对亮度。RGB输入及带矩阵/范围标记的YUV输入均转换成原基色高精度RGB后处理；16位FFV1输入已验证。输出为8位审片，量化不能保证任意相近的高光都有不同代码值。192 MiB是整个容器包括解码器的内存上限，验证以30秒1280×720 FFV1输入为资源边界，不宣称对任意编解码器及参考帧配置都有余量。

## Pitch-preserving dialogue timing

`POST /stretch-plans` accepts a `project_id` and one `maps` entry per timeline
occurrence. A map contains two to sixteen `{source_ms, picture_ms}` points,
relative to the trimmed source and the start of that occurrence's output.
Both axes start at zero, and the last source point equals the trim duration.
For example, a 500 ms take can use `[{"source_ms":0,"picture_ms":0},
{"source_ms":200,"picture_ms":300},{"source_ms":500,"picture_ms":650}]`.
Each span needs at least 80 ms on both axes and a speed between 0.5 and 2.
One to eight occurrences and at most thirty output seconds are supported.

`GET /stretch-plans/{id}` inspects the frozen timing. The `/render` and
`/audition` POST routes accept a new `destination`, optional `at_sample` and
`samples` on the 48 kHz output clock, and `block_frames` from 1 to 65536.
They deliver stereo float WAV, preserving source trims, occurrence gain and
joint-channel phase relationships. Timing maps do not change pitch: waveform
alignment preserves voiced periodicity while protected attack neighbourhoods
avoid duplicating short onsets. This is a bounded waveform overlap processor,
not a speech model or a claim to transparent processing of arbitrary music.

Synthesis follows the saved picture clock, retaining internal source/picture
anchors. An audition is cropped from that complete synthesis history, so it
matches complete delivery regardless of block size or later timeline edits.
Decoded originals are materialized once per occurrence to temporary disk;
only local analysis windows and output blocks remain in memory. Sources are
checked before decoding and before publication. Relocated verified originals
remain usable, occupied destinations remain protected and failures remove
private temporary files. Ordinary retiming keeps its pitch-changing contract.

## 多采样率声音交付

`POST /resample-plans` 接收 `{project_id,rate}`，目标 rate 为44100、48000或96000；
`GET /resample-plans/{id}` 重开冻结版本。原片第一条音轨可为单声道或立体声，
8–192 kHz、最长60秒；单声道同幅复制至左右。时间线支持最多8次剪入、总长30秒。
这条流程保持节目时长和原有音调，不执行普通变速、对白拉伸、响度母带或降噪。

时间线毫秒边界在一条目标采样时钟上四舍五入（半值向后），不逐段累计舍入误差。
每个输出采样按它在该次剪入中的真实时间取原片；原片裁切位置即使落在两个原生
采样之间，也不先挤到原生采样网格。重建只使用裁切范围内的原生采样点，范围外
视为零，保留各次剪入音量、双声道相位及相对强弱，不做独立归一化或削波。
转换保留低于两端较低奈奎斯特频率90%的可听带宽，抑制降采样折叠，支持
非整数比例并补偿滤波时延。裁切边缘允许有限长带限重建的自然振铃。

`POST /resample-plans/{id}/render|audition` 接收 `destination`、可选 `at_sample`、
`samples`、`block_frames`（1–65536），以目标时钟计数，输出目标采样率的双声道
float WAV。任意中段试听与整段文件同位置逐采样一致，处理块大小只影响I/O。
每次请求中每次剪入原片只顺序解码一次至临时磁盘，按原生采样率重建，不经过
固定48kHz中间转换。192 MiB交付机可处理30秒、8次剪入、最高192kHz原片，
内存不保留整段PCM。保存方案后编辑时间线不改变旧版，支持验证后的原片回连；
源变化拒绝、目标（含悬空链接）保护和失败时私有文件清理仍有效。

## 标记短爆点的声音修复

`POST /repair-marks` 接收 `{asset_id,intervals,expected_revision}`。
intervals 是原片48kHz采样时钟上的半开区间列表，例如 `[[12013,12157]]`；
标记者确认这些采样已损坏，区间外的采样仍可信。仅支持48kHz双声道原片，
最长60秒，最多32处，每处1–384采样，首尾及相邻缺损间需有512个完整采样。
重复或陈旧修订不覆盖后来标记；无标记素材保持原声音。

`POST /repair-plans` 接收 `{project_id}`，冻结剪辑和每份原片的标记修订。
`GET /repair-plans/{id}` 读取旧版；`POST /repair-plans/{id}/render|audition`
接收 `destination`、可选 `at_sample`、`samples`、`block_frames`（1–65536），
交付双声道48kHz float WAV。最多8次剪入、节目30秒，内存上限192 MiB。

修复在原片位置完成，然后按各次裁切和音量进入节目；镜头边界或短试听切入
缺损中间时，仍使用完整原片中的可信两侧上下文。同一个原片区间重复剪入，
修复波形在应用各次音量前应相同。任意试听逐采样等于完整交付切片，改变I/O块
不改变修复。短缺损用两侧波形关系估计，保留稳定周期与两声道相位、相对强弱；
不能把反相声道相加后当作无声，也不能复制附近完整话头代替缺损。标记外采样
只应用约定的剪入音量，不移动、拉伸、淡化或重新归一。该能力是短缺损估计，
无法承诺找回任意已丢失内容；数值不稳定时拒绝发布，不用削波掩盖重建错误。

方案保存后继续改标记或剪辑不影响旧版。每次请求每次剪入原片只顺序解码一次到
私有磁盘，修复上下文和工作内存有界，不常驻完整节目PCM。保留原片回连、
源变更拒绝、已有目标（包括悬空链接）保护和失败清理，普通降噪、房间处理、
对白对齐及采样率交付继续按各自约定工作。

## 回放参考串音消除

现场扬声器回放会以短延迟、多条正负反射进入话筒。`POST /echo-profiles`
接收 `microphone_id`（48kHz立体声话筒原片）、`reference_id`（48kHz单声道回放
参考）、`start_ms`、`end_ms` 和 `offset_samples`（默认0）。两份原片最长60秒。
操作者选择500–2000ms只有回放、没有现场发言的校准区间。参考坐标等于话筒
原片采样坐标加offset_samples，offset在正负48000之间。两录音时钟相同，
房间路径稳定；支持从该对齐位置起256采样内的有符号因果反射。参考须有足够
独立激励，静音或单一纯音不能用于辨识完整路径；不符校准假设则明确拒绝。

`POST /echo-plans` 接收 `project_id` 和与各次剪入对应的 `profile_ids`。
每条剪入的话筒原片必须匹配其校准身份，最多8次剪入、共30秒。方案冻结
完整校准系数、参考身份、原片裁切和音量；GET `/echo-plans/{id}` 或
`/echo-profiles/{id}` 可重开。新增校准、后续时间线编辑不改变旧版。

POST `/echo-plans/{id}/render|audition` 接收新 `destination`，可选
`at_sample`、`samples`、`block_frames`（1–16384），输出双声道48kHz float WAV。
先在原片时钟扣除参考经房间路径后的声音，再裁切和施加本次音量；参考范围外
视为零，裁切边缘仍使用原片位置所需的参考历史，不把节目/试听开头当作试播
开头。现场人声即使与回放同时发生，也不重新用混合声音学习路径，不能把
整段门控静音或左右分别归一化。任意试听逐采样等于全片切片，块大小只影响I/O。
192MiB交付机可完成最大节目，每次剪入话筒只顺序解码一次，每份参考每请求
只解码一次，临时磁盘承载展开音频；不把完整PCM常驻内存。支持验证后的原片
回连，源变化拒绝、目标含悬空链接保护和失败清理保持有效。该功能不自动
检测叠说区间，不跟踪移动话筒、长混响或不同步时钟，也不改变普通降噪/混音。

## 双镜头平面拼接

两个固定、已同步的相机拍摄同一个平面，交付静音 sRGB 无损 FFV1 MKV；不做视差补偿、自动找点或声音混合。
原片视频原生尺寸相同，32×32 至 640×360，按 25 fps 取样。

- `POST /stitch-profiles`：`left_asset_id`、`right_asset_id`、`matches`、可选 `threshold_px`（默认1，范围0.01–3）。
  6–12 对 `[right_x,right_y,left_x,left_y]` 为原生像素中心坐标。标定支持少量误标，必须有至少6个且过半数的可靠点，双向像素误差均在容限内；空间退化、无法达成共识、传感器内跨地平线均拒绝，不保存半份标定。
- `POST /stitch-plans`：`profile_id`、`left_start_ms`、`right_start_ms`、`duration_ms`、`canvas`。
  两路独立入点均为40ms整数倍，节目最长30秒。canvas为左相机像素坐标中的 `{x,y,width,height}`，输出像素中心从 `(x,y)` 开始，最大960×540；不能有两路都未覆盖的像素。
- `GET /stitch-profiles/{id}`、`GET /stitch-plans/{id}` 可重开冻结版本。
- `POST /stitch-plans/{id}/render|preview`：新 `destination`、可选 `at_frame`、`frame_count`。
  右图按平面透视关系映射到左图坐标；双线性采样、按各自有效图边距离羽化均在线性光中进行，再编码sRGB。
  单路覆盖区保留该路内容；预览逐帧等于完整交付同位置，不能重新定标或把预览开头当原片起点。
  两份原片各顺序解码一次到临时磁盘，内存不随片长增长；30秒640×360配对、640×360画布在192 MiB容器运行。
  后续新标定不改变旧方案，回连按原素材身份解析，源变化拒绝，目标禁止覆盖，失败清除私有临时文件。


## Full-sphere rectilinear view delivery

`POST /sphere-plans` freezes a full 2:1 equirectangular video asset, a
`start_ms` and `duration_ms` cut on 40 ms boundaries, even output `width` and
`height` (32–640 by 32–360), `horizontal_fov` (20–140 degrees), and two to
64 `keys`. A key is `{at_ms, quaternion:[x,y,z,w]}` relative to the cut,
including both endpoints. Nonzero finite quaternions are normalized. World
axes are right/up/forward; the quaternion actively maps a camera ray into
world space. Source longitude zero is forward at the horizontal centre;
positive longitude goes right, latitude increases upward. Source dimensions
are 2:1, up to 640×320. These are full-sphere images, not planar stitch inputs.

Adjacent orientations follow the shortest constant-angular-speed spherical
rotation; opposite quaternion signs describe identical orientations. An exact
180-degree turn needs an intermediate operator key to resolve its ambiguity.
At each 25 fps frame midpoint the camera emits a pixel-centred rectilinear
view; horizontal FOV and output aspect determine vertical coverage. Source
pixels are samples at cell centres. Longitude wraps, and interpolation across
a pole continues on the opposite meridian. Bilinear sampling operates on
linear sRGB light, then returns 8-bit sRGB. There is no exposure adjustment.

`GET /sphere-plans/{id}` reopens the immutable version. POST its `/render` or
`/preview` route with a new `destination` and optional `at_frame,frame_count`
to deliver silent lossless FFV1/MKV. A preview uses the same cut-relative
camera clock and source frame as that slice of the complete delivery. Plans
last at most 30 seconds. Originals are decoded once to a private disk spool;
only bounded frame arrays stay in memory. Verified original relinking, source
change rejection, occupied destination protection and failure cleanup apply.
Existing video and sound workflows remain independent.

## 陀螺仪辅助卷帘快门校正

`POST /rolling-plans` 接收 `asset_id,start_ms,duration_ms,horizontal_fov,zoom,
readout_ms,direction,gyro`。原片为无畸变针孔相机的视频，原生偶数尺寸
32×32 至640×360、宽高比1–2，按25fps取帧；只校正纯旋转的逐行曝光扭曲，
不补偿平移视差、镜头畸变或运动模糊，也不平滑帧间真实转头。
剪入及长度为40ms整倍数，最长30秒。水平视场40–100度，zoom为1–2的
中心裁切放大，readout_ms为0–20ms整帧读出时间；direction为
`top-to-bottom` 或 `bottom-to-top`。

相机坐标为右、下、前。gyro有2–4096条严格递增的
`{at_ms,radians_per_second:[x,y,z]}`，时间在原片0–60000ms时钟上，必须覆盖
整个剪入。角速度是相机本体坐标中的弧度每秒，每条保持到下一条；向量长度
至多2。首条姿态可任意置为单位，后续随本体旋转累积。每帧时刻为原片
`start_ms+(frame+.5)*40`，交付重建这一时刻的全局快门视图。
源像素中心所在行y的曝光时刻等于该帧时刻加
`sign*((y+.5)/height-.5)*readout_ms`，反向读出sign为-1。
输出像素中心经中心zoom裁切后代表同一针孔相机的光线，必须找到它在逐行
曝光原片中的对应位置，行时刻属于最终命中的原片行；不能以输出行替代。

校正后每个像素需有完整的原片支持，zoom不足或不能建立可信对应时明确拒绝，
不复制边缘、不填黑也不偷偷加大裁切；保存前检查全片，不落半份方案。
采样按线性sRGB双线性插值再编码，零读出、zoom=1应逐像素保留原片。
`GET /rolling-plans/{id}` 重开冻结版本；`POST /rolling-plans/{id}/render|preview`
接收新destination及可选at_frame、frame_count，输出静音无损FFV1/MKV。
任意中段预览与完整交付同位置逐帧相同，后续新方案和普通编辑不改变旧版。
每次请求原片顺序解码一次到私有磁盘，仅保存有界帧数组；最长节目在192MiB
运行环境完成。原片回连和变化拒绝、目标含悬空链接保护、失败私有文件清理有效。

## 单镜头畸变标定与去畸变交付

普通针孔相机的固定镜头可用原生传感器到理想无畸变图的对应点标定。
这不是双镜头平面单应，也不是球面展开或卷帘快门校正。

- `POST /lens-profiles` 接收 `asset_id,focal_px,centre,matches,threshold_px`。
  原生偶数尺寸32×32到640×360；focal_px是以像素计的已知焦距，范围为原生
  宽度的0.5–2倍；centre为原生像素中心坐标，须在传感器中央一半区域。
  matches为8–16组 `[sensor_x,sensor_y,ideal_x,ideal_y]`，threshold_px为
  理想图上的欧氏像素容限，默认0.25、范围0.01–2。可有少量误标；至少8组
  且过半数应共同解释完整的径向与偏心畸变，秩亏或不可信共识明确拒绝。
- 归一化坐标为 `(pixel-centre)/focal_px`。模型定义传感器到理想图的关系：
  `u=x+x*(k1*r²+k2*r⁴)+2*p1*x*y+p2*(r²+2*x²)`，
  `v=y+y*(k1*r²+k2*r⁴)+p1*(r²+2*y²)+2*p2*x*y`。
  仅支持整个传感器上的位移Jacobian范数可保守界定小于0.7的温和畸变；
  超范围或不满足单值映射保证的标定不落库。系数顺序为k1,k2,p1,p2。
- `POST /lens-plans` 接收 `profile_id,start_ms,duration_ms,zoom`。
  原片入点、长度为40ms整倍数，最长30秒；zoom为1–2、围绕光学中心的
  理想图中心裁切放大，输出保持原生尺寸。每个输出像素都必须能反查到
  完整传感器支持，裁切不足明确拒绝，不自动放大、不填黑、不复制边缘。
- `GET /lens-profiles/{id}`、`GET /lens-plans/{id}` 重开冻结版本；后续新标定
  或普通编辑不改变旧方案。`POST /lens-plans/{id}/render|preview` 接收新
  destination及可选at_frame、frame_count，交付静音sRGB无损FFV1 MKV。
  原生像素中心、线性光双线性采样、原片裁切时钟和25fps约定有效；任意
  中段预览逐帧等于完整交付同位置。零畸变、zoom=1逐像素保留输入。
  每请求顺序解码一次到私有磁盘，仅保留有界帧数组，最长640×360节目在
  192MiB交付机完成。原片回连、变化拒绝、已有目标含悬空链接保护、失败
  私有文件清理有效，其他视频声音工作流继续遵守各自约定。
