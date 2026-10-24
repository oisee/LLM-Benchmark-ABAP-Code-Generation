// Build immutable library output once before starting parallel compiler workers.
// This evaluates no generated ABAP and performs no model calls.
import {createEngine} from './engine.mjs';
await createEngine();
