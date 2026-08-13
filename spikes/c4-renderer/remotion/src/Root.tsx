import { AbsoluteFill, Composition, Sequence } from "remotion";

const FPS = 30;
const SLIDE_SECONDS = 5;
const SLIDES = [
  { title: "멀티에이전트 SNS", subtitle: "자율 성장 엔진이 뭐냐면" },
  { title: "영상도 코드로 만든다", subtitle: "자막 + 슬라이드 + TTS 합성" },
  { title: "생성형 비디오 모델?", subtitle: "안 씁니다. 템플릿 코드 합성." },
];
// safe area 900×1400 중앙 박스
const SAFE_MARGIN_X = (1080 - 900) / 2;
const SAFE_MARGIN_V = (1920 - 1400) / 2;

const Slide: React.FC<{ title: string; subtitle: string }> = ({ title, subtitle }) => (
  <AbsoluteFill
    style={{
      backgroundColor: "#101828",
      fontFamily: "Malgun Gothic, sans-serif",
      justifyContent: "center",
      alignItems: "center",
    }}
  >
    <div style={{ color: "#F9FAFB", fontSize: 88, fontWeight: 700, marginTop: -240 }}>
      {title}
    </div>
    <div
      style={{
        position: "absolute",
        bottom: SAFE_MARGIN_V,
        left: SAFE_MARGIN_X,
        right: SAFE_MARGIN_X,
        textAlign: "center",
        color: "#FFFFFF",
        fontSize: 64,
        fontWeight: 700,
        textShadow: "0 0 8px #000",
      }}
    >
      {subtitle}
    </div>
  </AbsoluteFill>
);

const Shorts: React.FC = () => (
  <AbsoluteFill>
    {SLIDES.map((s, i) => (
      <Sequence key={i} from={i * SLIDE_SECONDS * FPS} durationInFrames={SLIDE_SECONDS * FPS}>
        <Slide {...s} />
      </Sequence>
    ))}
  </AbsoluteFill>
);

export const Root: React.FC = () => (
  <Composition
    id="Shorts"
    component={Shorts}
    durationInFrames={SLIDES.length * SLIDE_SECONDS * FPS}
    fps={FPS}
    width={1080}
    height={1920}
  />
);
