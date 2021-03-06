# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Artifact conversion to and from Python TensorFlow and Keras."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import json
import os
import tempfile

import h5py
import keras
import tensorflow as tf

from tensorflowjs import quantization
from tensorflowjs import version
from tensorflowjs.converters import keras_h5_conversion as conversion
from tensorflowjs.converters import keras_tfjs_loader
from tensorflowjs.converters import tf_saved_model_conversion

def dispatch_keras_h5_to_tensorflowjs_conversion(
    h5_path, output_dir=None, quantization_dtype=None,
    split_weights_by_layer=False):
  """Converts a Keras HDF5 saved-model file to TensorFlow.js format.

  Auto-detects saved_model versus weights-only and generates the correct
  json in either case. This function accepts Keras HDF5 files in two formats:
    - A weights-only HDF5 (e.g., generated with Keras Model's `save_weights()`
      method),
    - A topology+weights combined HDF5 (e.g., generated with
      `keras.model.save_model`).

  Args:
    h5_path: path to an HDF5 file containing keras model data as a `str`.
    output_dir: Output directory to which the TensorFlow.js-format model JSON
      file and weights files will be written. If the directory does not exist,
      it will be created.
    quantization_dtype: The quantized data type to store the weights in
      (Default: `None`).
    split_weights_by_layer: Whether to split the weights into separate weight
      groups (corresponding to separate binary weight files) layer by layer
      (Default: `False`).

  Returns:
    (model_json, groups)
      model_json: a json dictionary (empty if unused) for model topology.
        If `h5_path` points to a weights-only HDF5 file, this return value
        will be `None`.
      groups: an array of weight_groups as defined in tfjs weights_writer.
  """
  if not os.path.exists(h5_path):
    raise ValueError('Nonexistent path to HDF5 file: %s' % h5_path)
  elif os.path.isdir(h5_path):
    raise ValueError(
        'Expected path to point to an HDF5 file, but it points to a '
        'directory: %s' % h5_path)

  h5_file = h5py.File(h5_path)
  if 'layer_names' in h5_file.attrs:
    model_json = None
    groups = conversion.h5_weights_to_tfjs_format(
        h5_file, split_by_layer=split_weights_by_layer)
  else:
    model_json, groups = conversion.h5_merged_saved_model_to_tfjs_format(
        h5_file, split_by_layer=split_weights_by_layer)

  if output_dir:
    if os.path.isfile(output_dir):
      raise ValueError(
          'Output path "%s" already exists as a file' % output_dir)
    elif not os.path.isdir(output_dir):
      os.makedirs(output_dir)
    conversion.write_artifacts(
        model_json, groups, output_dir, quantization_dtype)

  return model_json, groups


def dispatch_keras_saved_model_to_tensorflowjs_conversion(
    keras_saved_model_path, output_dir, quantization_dtype=None,
    split_weights_by_layer=False):
  """Converts tf.keras model saved in the SavedModel format to tfjs format.

  Note that the SavedModel format exists in tf.keras, but not in
  keras-team/keras.

  Args:
    keras_saved_model_path: path to a folder in which the
      assets/saved_model.json can be found. This is usually a subfolder
      that is under the folder passed to
      `tf.contrib.saved_model.save_keras_model()` and has a Unix epoch time
      as its name (e.g., 1542212752).
    output_dir: Output directory to which the TensorFlow.js-format model JSON
      file and weights files will be written. If the directory does not exist,
      it will be created.
    quantization_dtype: The quantized data type to store the weights in
      (Default: `None`).
    split_weights_by_layer: Whether to split the weights into separate weight
      groups (corresponding to separate binary weight files) layer by layer
      (Default: `False`).
  """
  with tf.Graph().as_default(), tf.Session():
    model = tf.contrib.saved_model.load_keras_model(keras_saved_model_path)

    # Save model temporarily in HDF5 format.
    temp_h5_path = tempfile.mktemp(suffix='.h5')
    model.save(temp_h5_path)
    assert os.path.isfile(temp_h5_path)

    dispatch_keras_h5_to_tensorflowjs_conversion(
        temp_h5_path,
        output_dir,
        quantization_dtype=quantization_dtype,
        split_weights_by_layer=split_weights_by_layer)

    # Delete temporary .h5 file.
    os.remove(temp_h5_path)


def dispatch_tensorflowjs_to_keras_h5_conversion(config_json_path, h5_path):
  """Converts a Keras Model from tensorflowjs format to H5.

  Args:
    config_json_path: Path to the JSON file that includes the model's
      topology and weights manifest, in tensorflowjs format.
    h5_path: Path for the to-be-created Keras HDF5 model file.

  Raises:
    ValueError, if `config_json_path` is not a path to a valid JSON
      file, or if h5_path points to an existing directory.
  """
  if os.path.isdir(config_json_path):
    raise ValueError(
        'For input_type=tensorflowjs & output_format=keras, '
        'the input path should be a model.json '
        'file, but received a directory.')
  if os.path.isdir(h5_path):
    raise ValueError(
        'For input_type=tensorflowjs & output_format=keras, '
        'the output path should be the path to an HDF5 file, '
        'but received an existing directory (%s).' % h5_path)

  # Verify that config_json_path points to a JSON file.
  with open(config_json_path, 'rt') as f:
    try:
      json.load(f)
    except (ValueError, IOError):
      raise ValueError(
          'For input_type=tensorflowjs & output_format=keras, '
          'the input path is expected to contain valid JSON content, '
          'but cannot read valid JSON content from %s.' % config_json_path)

  with tf.Graph().as_default(), tf.Session():
    model = keras_tfjs_loader.load_keras_model(config_json_path)
    model.save(h5_path)
    print('Saved Keras model to HDF5 file: %s' % h5_path)


def _standardize_input_output_formats(input_format, output_format):
  """Standardize input and output formats.

  Args:
    input_format: Input format as a string.
    output_format: Output format as a string.

  Returns:
    A `tuple` of two strings:
      (standardized_input_format, standardized_output_format).
  """
  # https://github.com/tensorflow/tfjs/issues/1292: Remove the logic for the
  # explicit error message of the deprecated model type name 'tensorflowjs'
  # at version 1.1.0.
  if input_format == 'tensorflowjs':
    raise ValueError(
        '--input_format=tensorflowjs has been deprecated. '
        'Use --input_format=tfjs_layers_model instead.')

  input_format_is_keras = (
      input_format == 'keras' or input_format == 'keras_saved_model')
  input_format_is_tf = (
      input_format == 'tf_frozen_model' or input_format == 'tf_hub' or
      input_format == 'tf_saved_model' or
      input_format == 'tf_session_bundle')
  if output_format is None:
    # If no explicit output_format is provided, infer it from input format.
    if input_format_is_keras:
      output_format = 'tfjs_layers_model'
    elif input_format_is_tf:
      output_format = 'tfjs_graph_model'
    elif input_format == 'tfjs_layers_model':
      output_format = 'keras'
  elif output_format == 'tensorflowjs':
    # https://github.com/tensorflow/tfjs/issues/1292: Remove the logic for the
    # explicit error message of the deprecated model type name 'tensorflowjs'
    # at version 1.1.0.
    if input_format_is_keras:
      raise ValueError(
          '--output_format=tensorflowjs has been deprecated under '
          '--input_format=%s. Use --output_format=tfjs_layers_model '
          'instead.' % input_format)
    elif input_format_is_tf:
      raise ValueError(
          '--output_format=tensorflowjs has been deprecated under '
          '--input_format=%s. Use --output_format=tfjs_graph_model '
          'instead.' % input_format)

  return (input_format, output_format)


def setup_arugments():
  parser = argparse.ArgumentParser('TensorFlow.js model converters.')
  parser.add_argument(
      'input_path',
      nargs='?',
      type=str,
      help='Path to the input file or directory. For input format "keras", '
      'an HDF5 (.h5) file is expected. For input format "tensorflow", '
      'a SavedModel directory, session bundle directory, frozen model file, '
      'or TF-Hub module is expected.')
  parser.add_argument(
      'output_path', nargs='?', type=str, help='Path for all output artifacts.')
  parser.add_argument(
      '--input_format',
      type=str,
      required=False,
      default='tf_saved_model',
      choices=set(['keras', 'keras_saved_model',
                   'tf_saved_model', 'tf_session_bundle', 'tf_frozen_model',
                   'tf_hub', 'tfjs_layers_model', 'tensorflowjs']),
      help='Input format. '
      'For "keras", the input path can be one of the two following formats:\n'
      '  - A topology+weights combined HDF5 (e.g., generated with'
      '    `keras.model.save_model()` method).\n'
      '  - A weights-only HDF5 (e.g., generated with Keras Model\'s '
      '    `save_weights()` method). \n'
      'For "keras_saved_model", the input_path must point to a subfolder '
      'under the saved model folder that is passed as the argument '
      'to tf.contrib.save_model.save_keras_model(). '
      'The subfolder is generated automatically by tensorflow when '
      'saving tf.keras model in the SavedModel format. It is usually named '
      'as a Unix epoch time (e.g., 1542212752).\n'
      'For "tf" formats, a SavedModel, frozen model, session bundle model, '
      ' or TF-Hub module is expected.')
  parser.add_argument(
      '--output_format',
      type=str,
      required=False,
      choices=set(['keras', 'tfjs_layers_model', 'tfjs_graph_model',
                   'tensorflowjs']),
      help='Output format. Default: tfjs_graph_model.')
  parser.add_argument(
      '--output_node_names',
      type=str,
      help='The names of the output nodes, separated by commas. E.g., '
      '"logits,activations". Applicable only if input format is '
      '"tf_saved_model" or "tf_session_bundle".')
  parser.add_argument(
      '--signature_name',
      type=str,
      help='Signature of the TF-Hub module to load. Applicable only if input'
      ' format is "tf_hub".')
  parser.add_argument(
      '--saved_model_tags',
      type=str,
      default='serve',
      help='Tags of the MetaGraphDef to load, in comma separated string '
      'format. Defaults to "serve". Applicable only if input format is '
      '"tf_saved_model".')
  parser.add_argument(
      '--quantization_bytes',
      type=int,
      choices=set(quantization.QUANTIZATION_BYTES_TO_DTYPES.keys()),
      help='How many bytes to optionally quantize/compress the weights to. 1- '
      'and 2-byte quantizaton is supported. The default (unquantized) size is '
      '4 bytes.')
  parser.add_argument(
      '--split_weights_by_layer',
      action='store_true',
      help='Applicable to keras input_format only: Whether the weights from '
      'different layers are to be stored in separate weight groups, '
      'corresponding to separate binary weight files. Default: False.')
  parser.add_argument(
      '--version',
      '-v',
      dest='show_version',
      action='store_true',
      help='Show versions of tensorflowjs and its dependencies')
  parser.add_argument(
      '--skip_op_check',
      type=bool,
      default=False,
      help='Skip op validation for TensorFlow model conversion.')
  parser.add_argument(
      '--strip_debug_ops',
      type=bool,
      default=True,
      help='Strip debug ops (Print, Assert, CheckNumerics) from graph.')
  return parser.parse_args()


def main():
  FLAGS = setup_arugments()
  if FLAGS.show_version:
    print('\ntensorflowjs %s\n' % version.version)
    print('Dependency versions:')
    print('  keras %s' % keras.__version__)
    print('  tensorflow %s' % tf.__version__)
    return

  if FLAGS.input_path is None:
    raise ValueError(
        'Error: The input_path argument must be set. '
        'Run with --help flag for usage information.')

  input_format, output_format = _standardize_input_output_formats(
      FLAGS.input_format, FLAGS.output_format)

  quantization_dtype = (
      quantization.QUANTIZATION_BYTES_TO_DTYPES[FLAGS.quantization_bytes]
      if FLAGS.quantization_bytes else None)

  if (FLAGS.output_node_names and
      input_format not in
      ('tf_saved_model', 'tf_session_bundle', 'tf_frozen_model')):
    raise ValueError(
        'The --output_node_names flag is applicable only to input formats '
        '"tf_saved_model", "tf_session_bundle" and "tf_frozen_model", '
        'but the current input format is "%s".' % FLAGS.input_format)

  if FLAGS.signature_name and input_format != 'tf_hub':
    raise ValueError(
        'The --signature_name is applicable only to "tf_hub" input format, '
        'but the current input format is "%s".' % input_format)

  # TODO(cais, piyu): More conversion logics can be added as additional
  #   branches below.
  if input_format == 'keras' and output_format == 'tfjs_layers_model':
    dispatch_keras_h5_to_tensorflowjs_conversion(
        FLAGS.input_path, output_dir=FLAGS.output_path,
        quantization_dtype=quantization_dtype,
        split_weights_by_layer=FLAGS.split_weights_by_layer)
  elif (input_format == 'keras_saved_model' and
        output_format == 'tfjs_layers_model'):
    dispatch_keras_saved_model_to_tensorflowjs_conversion(
        FLAGS.input_path, FLAGS.output_path,
        quantization_dtype=quantization_dtype,
        split_weights_by_layer=FLAGS.split_weights_by_layer)
  elif (input_format == 'tf_saved_model' and
        output_format == 'tfjs_graph_model'):
    tf_saved_model_conversion.convert_tf_saved_model(
        FLAGS.input_path, FLAGS.output_node_names,
        FLAGS.output_path, saved_model_tags=FLAGS.saved_model_tags,
        quantization_dtype=quantization_dtype,
        skip_op_check=FLAGS.skip_op_check,
        strip_debug_ops=FLAGS.strip_debug_ops)
  elif (input_format == 'tf_session_bundle' and
        output_format == 'tfjs_graph_model'):
    tf_saved_model_conversion.convert_tf_session_bundle(
        FLAGS.input_path, FLAGS.output_node_names,
        FLAGS.output_path, quantization_dtype=quantization_dtype,
        skip_op_check=FLAGS.skip_op_check,
        strip_debug_ops=FLAGS.strip_debug_ops)
  elif (input_format == 'tf_frozen_model' and
        output_format == 'tfjs_graph_model'):
    tf_saved_model_conversion.convert_tf_frozen_model(
        FLAGS.input_path, FLAGS.output_node_names,
        FLAGS.output_path, quantization_dtype=quantization_dtype,
        skip_op_check=FLAGS.skip_op_check,
        strip_debug_ops=FLAGS.strip_debug_ops)
  elif (input_format == 'tf_hub' and
        output_format == 'tfjs_graph_model'):
    if FLAGS.signature_name:
      tf_saved_model_conversion.convert_tf_hub_module(
          FLAGS.input_path, FLAGS.output_path, FLAGS.signature_name,
          skip_op_check=FLAGS.skip_op_check,
          strip_debug_ops=FLAGS.strip_debug_ops)
    else:
      tf_saved_model_conversion.convert_tf_hub_module(
          FLAGS.input_path,
          FLAGS.output_path,
          skip_op_check=FLAGS.skip_op_check,
          strip_debug_ops=FLAGS.strip_debug_ops)
  elif (input_format == 'tfjs_layers_model' and
        output_format == 'keras'):
    dispatch_tensorflowjs_to_keras_h5_conversion(FLAGS.input_path,
                                                 FLAGS.output_path)

  else:
    raise ValueError(
        'Unsupported input_format - output_format pair: %s - %s' %
        (input_format, output_format))


if __name__ == '__main__':
  main()
