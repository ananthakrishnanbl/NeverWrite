import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Landing from './pages/Landing'
import TemplateDownload from './pages/TemplateDownload'
import ProfileList from './pages/ProfileList'
import Capture from './pages/Capture'
import Processing from './pages/Processing'
import GlyphReview from './pages/GlyphReview'
import StylePreview from './pages/StylePreview'
import Composer from './pages/Composer'
import Export from './pages/Export'
import BatchExport from './pages/BatchExport'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Landing />} />
        <Route path="/template" element={<TemplateDownload />} />
        <Route path="/profiles" element={<ProfileList />} />
        <Route path="/profiles/new" element={<Navigate to="/template" replace />} />
        <Route path="/profiles/:profileId/capture" element={<Capture />} />
        <Route path="/profiles/:profileId/processing/:jobId" element={<Processing />} />
        <Route path="/profiles/:profileId/glyphs" element={<GlyphReview />} />
        <Route path="/profiles/:profileId/preview" element={<StylePreview />} />
        <Route path="/profiles/:profileId/compose" element={<Composer />} />
        <Route path="/profiles/:profileId/export/:renderId" element={<Export />} />
        <Route path="/profiles/:profileId/export/batch/:batchId" element={<BatchExport />} />
      </Route>
    </Routes>
  )
}
