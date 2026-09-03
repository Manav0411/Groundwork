/**
 * The evidence surface: everything that presents an answer and its provenance.
 *
 * These components are purely presentational and take a `QueryResponse`
 * straight from the API. They open no client boundary, fetch nothing, and hold
 * no state, so the landing page can render them statically while the app
 * renders the same components from a live call.
 */
export { AnswerBody, CitationMarker } from "./AnswerBody";
export { AnswerCard } from "./AnswerCard";
export { CitationRail } from "./CitationRail";
export { Refusal } from "./Refusal";
export { TraceStrip } from "./TraceStrip";
export {
  citationsById,
  gradeStyle,
  isRefusal,
  refusalKind,
  sourceKey,
  sourceStyle,
  traceBars,
  type RefusalKind,
  type SourceKey,
  type TraceBar
} from "./provenance";
