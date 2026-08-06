import { Link, Route, Routes } from "react-router-dom";
import TaskDetail from "./pages/TaskDetail";
import Tasks from "./pages/Tasks";

function App() {
  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100">
      <nav className="border-b border-neutral-800 px-6 py-3">
        <div className="mx-auto flex max-w-5xl items-center gap-6">
          <Link to="/" className="text-xl font-bold">
            Jalebi
          </Link>
          <Link to="/" className="text-neutral-400 hover:text-neutral-100">
            Tasks
          </Link>
        </div>
      </nav>
      <main className="mx-auto max-w-5xl px-6 py-6">
        <Routes>
          <Route path="/" element={<Tasks />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
