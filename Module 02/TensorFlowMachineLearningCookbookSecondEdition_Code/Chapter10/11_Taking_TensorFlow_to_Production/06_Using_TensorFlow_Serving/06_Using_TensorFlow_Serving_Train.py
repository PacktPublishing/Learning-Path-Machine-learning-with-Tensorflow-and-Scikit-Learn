# -*- coding: utf-8 -*-
# Using TensorFlow Serving
#----------------------------------
#
# We show how to use "TensorFlow Serving", a model serving api from TensorFlow to serve a model.
#
# Pre-requisites:
#  - Visit https://www.tensorflow.org/serving/setup
#    and follow all the instructions on setting up TensorFlow Serving (including installing Bazel).
#
# The example we will productionalize is the spam/ham RNN
#    from Chapter 9 (RNNs), section 2

import os
import re
import io
import sys
import requests
import numpy as np
import tensorflow as tf
from zipfile import ZipFile
from tensorflow.python.framework import ops

ops.reset_default_graph()

# Define App Flags
tf.flags.DEFINE_string("storage_folder", "temp", "Where to store model and data.")
tf.flags.DEFINE_float('learning_rate', 0.0005, 'Initial learning rate.')
tf.flags.DEFINE_float('dropout_prob', 0.5, 'Per to keep probability for dropout.')
tf.flags.DEFINE_integer('epochs', 20, 'Number of epochs for training.')
tf.flags.DEFINE_integer('batch_size', 250, 'Batch Size for training.')
tf.flags.DEFINE_integer('rnn_size', 15, 'RNN feature size.')
tf.flags.DEFINE_integer('embedding_size', 25, 'Word embedding size.')
tf.flags.DEFINE_integer('min_word_frequency', 20, 'Word frequency cutoff.')
tf.flags.DEFINE_boolean('run_unit_tests', False, 'If true, run tests.')

FLAGS = tf.flags.FLAGS


# Define how to get data
def get_data(storage_folder=FLAGS.storage_folder, data_file="text_data.txt"):
    """
    This function gets the spam/ham data.  It will download it if it doesn't
    already exist on disk (at specified folder/file location).
    """
    # Make a storage folder for models and data
    if not os.path.exists(storage_folder):
        os.makedirs(storage_folder)

    if not os.path.isfile(os.path.join(storage_folder, data_file)):
        zip_url = 'http://archive.ics.uci.edu/ml/machine-learning-databases/00228/smsspamcollection.zip'
        r = requests.get(zip_url)
        z = ZipFile(io.BytesIO(r.content))
        file = z.read('SMSSpamCollection')
        # Format Data
        text_data = file.decode()
        text_data = text_data.encode('ascii', errors='ignore')
        text_data = text_data.decode().split('\n')

        # Save data to text file
        with open(os.path.join(storage_folder, data_file), 'w') as file_conn:
            for text in text_data:
                file_conn.write("{}\n".format(text))
    else:
        # Open data from text file
        text_data = []
        with open(os.path.join(storage_folder, data_file), 'r') as file_conn:
            for row in file_conn:
                text_data.append(row)
        text_data = text_data[:-1]
    text_data = [x.split('\t') for x in text_data if len(x) >= 1]
    [y_data, x_data] = [list(x) for x in zip(*text_data)]

    return x_data, y_data


# Create a text cleaning function
def clean_text(text_string):
    text_string = re.sub(r'([^\s\w]|_|[0-9])+', '', text_string)
    text_string = " ".join(text_string.split())
    text_string = text_string.lower()
    return text_string


# Test clean_text function
class clean_test(tf.test.TestCase):
    # Make sure cleaning function behaves correctly
    def clean_string_test(self):
        with self.test_session():
            test_input = '--TensorFlow\'s so Great! Don\t you think so?   '
            test_expected = 'tensorflows so great don you think so'
            test_out = clean_text(test_input)
            self.assertEqual(test_expected, test_out)


# Define RNN Model
def rnn_model(x_data_ph, vocab_size, embedding_size, rnn_size, dropout_keep_prob):
    # Create embedding
    embedding_mat = tf.Variable(tf.random_uniform([vocab_size, embedding_size], -1.0, 1.0))
    embedding_output = tf.nn.embedding_lookup(embedding_mat, x_data_ph)

    # Define the RNN cell
    cell = tf.contrib.rnn.BasicRNNCell(num_units=rnn_size)
    output, state = tf.nn.dynamic_rnn(cell, embedding_output, dtype=tf.float32)
    output = tf.nn.dropout(output, dropout_keep_prob)

    # Get output of RNN sequence
    output = tf.transpose(output, [1, 0, 2])
    last = tf.gather(output, int(output.get_shape()[0]) - 1)

    weight = tf.Variable(tf.truncated_normal([rnn_size, 2], stddev=0.1))
    bias = tf.Variable(tf.constant(0.1, shape=[2]))
    logits_out = tf.matmul(last, weight) + bias

    return logits_out


# Define accuracy function
def get_accuracy(logits, actuals):
    # Calulate if each output is correct
    batch_acc = tf.equal(tf.argmax(logits, 1), tf.cast(actuals, tf.int64))
    # Convert logical to float
    batch_acc = tf.cast(batch_acc, tf.float32)
    return batch_acc


# Define main program
def main(args):
    # Set verbosity to get more information from TensorFlow
    tf.logging.set_verbosity(tf.logging.INFO)

    # Create a visualizer object for Tensorboard viewing
    summary_writer = tf.summary.FileWriter('tensorboard', tf.get_default_graph())
    # Create tensorboard folder if not exists
    if not os.path.exists('tensorboard'):
        os.makedirs('tensorboard')

    # Set model parameters
    storage_folder = FLAGS.storage_folder
    learning_rate = FLAGS.learning_rate
    run_unit_tests = FLAGS.run_unit_tests
    epochs = FLAGS.epochs
    batch_size = FLAGS.batch_size
    rnn_size = FLAGS.rnn_size
    embedding_size = FLAGS.embedding_size
    min_word_frequency = FLAGS.min_word_frequency

    # Get text->spam/ham data
    x_data, y_data = get_data()

    # Clean texts
    x_data = [clean_text(x) for x in x_data]

    # Change texts into numeric vectors
    # Set a max sequence length for speeding up the computations.
    # But we can easily set "max_sequence_length = max([len(x) for x in x_data])" as well.
    max_sequence_length = 20
    vocab_processor = tf.contrib.learn.preprocessing.VocabularyProcessor(max_sequence_length,
                                                                         min_frequency=min_word_frequency)
    text_processed = np.array(list(vocab_processor.fit_transform(x_data)))

    # Save vocab processor (for loading and future evaluation)
    vocab_processor.save(os.path.join(storage_folder, "vocab"))

    # Shuffle and split data
    text_processed = np.array(text_processed)
    y_data = np.array([1 if x == 'ham' else 0 for x in y_data])
    shuffled_ix = np.random.permutation(np.arange(len(y_data)))
    x_shuffled = text_processed[shuffled_ix]
    y_shuffled = y_data[shuffled_ix]

    # Split train/test set
    ix_cutoff = int(len(y_shuffled) * 0.80)
    x_train, x_test = x_shuffled[:ix_cutoff], x_shuffled[ix_cutoff:]
    y_train, y_test = y_shuffled[:ix_cutoff], y_shuffled[ix_cutoff:]
    vocab_size = len(vocab_processor.vocabulary_)

    with tf.Graph().as_default():
        sess = tf.Session()
        # Define placeholders
        x_data_ph = tf.placeholder(tf.int32, [None, max_sequence_length], name='x_data_ph')
        y_output_ph = tf.placeholder(tf.int32, [None], name='y_output_ph')
        dropout_keep_prob = tf.placeholder(tf.float32, name='dropout_keep_prob')

        # Define Model
        rnn_model_outputs = rnn_model(x_data_ph, vocab_size, embedding_size, rnn_size, dropout_keep_prob)

        # Prediction
        # Although we won't use the following operation, we declare and name
        #   the probability outputs so that we can recall them later for evaluation
        rnn_prediction = tf.nn.softmax(rnn_model_outputs, name="probability_outputs")

        # Loss function
        losses = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=rnn_model_outputs, labels=y_output_ph)
        # Remember that for this loss function, logits=float32, labels=int32
        loss = tf.reduce_mean(losses, name="loss")

        # Model Accuracy Operation
        accuracy = tf.reduce_mean(get_accuracy(rnn_model_outputs, y_output_ph), name="accuracy")

        # Add scalar summaries for Tensorboard
        with tf.name_scope('Scalar_Summaries'):
            tf.summary.scalar('Loss', loss)
            tf.summary.scalar('Accuracy', accuracy)

        # Declare Optimizer/train step
        optimizer = tf.train.GradientDescentOptimizer(learning_rate)
        train_step = optimizer.minimize(loss)

        # Declare summary merging operation
        summary_op = tf.summary.merge_all()

        # Create a graph/Variable saving/loading operations
        saver = tf.train.Saver()

        init = tf.global_variables_initializer()
        sess.run(init)

        # Start training
        for epoch in range(epochs):

            # Shuffle training data
            shuffled_ix = np.random.permutation(np.arange(len(x_train)))
            x_train = x_train[shuffled_ix]
            y_train = y_train[shuffled_ix]
            num_batches = int(len(x_train) / batch_size) + 1
            #
            for i in range(num_batches):
                # Select train data
                min_ix = i * batch_size
                max_ix = np.min([len(x_train), ((i + 1) * batch_size)])
                x_train_batch = x_train[min_ix:max_ix]
                y_train_batch = y_train[min_ix:max_ix]

                # Run train step
                train_dict = {x_data_ph: x_train_batch,
                              y_output_ph: y_train_batch,
                              dropout_keep_prob: 0.5}
                _, summary = sess.run([train_step, summary_op], feed_dict=train_dict)

                summary_writer = tf.summary.FileWriter('tensorboard')
                summary_writer.add_summary(summary, i)

            # Run loss and accuracy for training
            temp_train_loss, temp_train_acc = sess.run([loss, accuracy], feed_dict=train_dict)

            test_dict = {x_data_ph: x_test, y_output_ph: y_test, dropout_keep_prob: 1.0}
            temp_test_loss, temp_test_acc = sess.run([loss, accuracy], feed_dict=test_dict)

            # Print Epoch Summary
            print('Epoch: {}, Train Loss:{:.2}, Train Acc: {:.2}'.format(epoch + 1, temp_train_loss, temp_train_acc))
            print('Epoch: {}, Test Loss: {:.2}, Test Acc: {:.2}'.format(epoch + 1, temp_test_loss, temp_test_acc))

            # Save model every epoch
            saver.save(sess, os.path.join(storage_folder, "model.ckpt"))

        # Save the finished model for TensorFlow Serving (pb file)
        # Here, it's our storage folder / version number
        out_path = os.path.join(tf.compat.as_bytes(os.path.join(storage_folder, '1')))
        print('Exporting finished model to : {}'.format(out_path))
        builder = tf.saved_model.builder.SavedModelBuilder(out_path)

        # Build the signature_def_map.
        classification_inputs = tf.saved_model.utils.build_tensor_info(x_data_ph)
        classification_outputs_classes = tf.saved_model.utils.build_tensor_info(rnn_model_outputs)

        classification_signature = (tf.saved_model.signature_def_utils.build_signature_def(
                inputs={tf.saved_model.signature_constants.CLASSIFY_INPUTS: classification_inputs},
                outputs={tf.saved_model.signature_constants.CLASSIFY_OUTPUT_CLASSES: classification_outputs_classes},
                method_name=tf.saved_model.signature_constants.CLASSIFY_METHOD_NAME)
        )

        tensor_info_x = tf.saved_model.utils.build_tensor_info(x_data_ph)
        tensor_info_y = tf.saved_model.utils.build_tensor_info(y_output_ph)

        prediction_signature = (
            tf.saved_model.signature_def_utils.build_signature_def(
                inputs={'texts': tensor_info_x},
                outputs={'scores': tensor_info_y},
                method_name=tf.saved_model.signature_constants.PREDICT_METHOD_NAME))

        legacy_init_op = tf.group(tf.tables_initializer(), name='legacy_init_op')
        builder.add_meta_graph_and_variables(
            sess, [tf.saved_model.tag_constants.SERVING],
            signature_def_map={
                'predict_spam':
                    prediction_signature,
                tf.saved_model.signature_constants.DEFAULT_SERVING_SIGNATURE_DEF_KEY:
                    classification_signature,
            },
            legacy_init_op=legacy_init_op)

        builder.save()

        print('Done exporting!')


# Run main module/tf App
if __name__ == "__main__":
    cmd_args = sys.argv
    if len(cmd_args) > 1 and cmd_args[1] == 'test':
        # Perform unit tests
        tf.test.main(argv=cmd_args[1:])
    else:
        # Run TF App
        tf.app.run(main=None, argv=cmd_args)
