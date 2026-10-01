"use client";

import WorkspaceV2 from "./WorkspaceV2";

// ActivityTrace is rendered directly by WorkspaceV2 from structured SSE
// activity events. Keep this component as the stable public workspace entry
// point; it intentionally has zero DOM scraping or routing state of its own.
export default function WorkspaceExperience(){
 return <WorkspaceV2/>;
}
