import os
import torch
import logging
import numpy as np
from collections import Counter

# from fairseq.data import Dictionary, LanguagePairDataset
# from fairseq.tasks import FairseqTask, LegacyFairseqTask, register_task
from fairseq.data import Dictionary, LanguagePairDataset, FairseqDataset, data_utils, iterators
from fairseq.tasks import LegacyFairseqTask, register_task

logger = logging.getLogger(__name__)


@register_task('siamese_classification')
class SiameseClassificationTask(LegacyFairseqTask):

    @staticmethod
    def add_args(parser):
        # Add some command-line arguments for specifying where the data is
        # located and the maximum supported input length.
        parser.add_argument('data', metavar='FILE',
                            help='file prefix for data')
        parser.add_argument('--max-positions', default=1024, type=int,
                            help='max input length')

    @classmethod
    def setup_task(cls, args, **kwargs):
        # Here we can perform any setup required for the task. This may include
        # loading Dictionaries, initializing shared Embedding layers, etc.
        # In this case we'll just load the Dictionaries.
        input_vocab = Dictionary.load(os.path.join(args.data, 'dict.input.txt'))
        label_vocab = Dictionary.load(os.path.join(args.data, 'dict.label.txt'))
        print('| [input] dictionary: {} types'.format(len(input_vocab)))
        print('| [label] dictionary: {} types'.format(len(label_vocab)))

        return SiameseClassificationTask(args, input_vocab, label_vocab)

    def __init__(self, args, input_vocab, label_vocab):
        super().__init__(args)
        self.input_vocab = input_vocab
        self.label_vocab = label_vocab

    def load_dataset(self, split, **kwargs):
        """Load a given dataset split (e.g., train, valid, test)."""

        prefix = os.path.join(self.args.data, '{}.input-label'.format(split))

        # Read input sentences.
        sentences, lengths = [], []
        with open(prefix + '.input', encoding='utf-8') as file:
            for line in file:
                sentence = line.strip()

                # Tokenize the sentence, splitting on spaces
                tokens = self.input_vocab.encode_line(
                    sentence, add_if_not_exist=False,
                )

                sentences.append(tokens)
                lengths.append(tokens.numel())

        # Read labels.
        labels = []
        with open(prefix + '.label', encoding='utf-8') as file:
            for line in file:
                label = line.strip()
                labels.append(
                    # Convert label to a numeric ID.
                    torch.LongTensor([self.label_vocab.add_symbol(label)])
                )

        assert len(sentences) == len(labels)
        print('| {} {} {} examples'.format(self.args.data, split, len(sentences)))

        # We reuse LanguagePairDataset since classification can be modeled as a
        # sequence-to-sequence task where the target sequence has length 1.
        self.datasets[split] = LanguagePairDataset(
            src=sentences,
            src_sizes=lengths,
            src_dict=self.input_vocab,
            tgt=labels,
            tgt_sizes=torch.ones(len(labels)),  # targets have length 1
            tgt_dict=self.label_vocab,
            left_pad_source=False,
            # Since our target is a single class label, there's no need for
            # teacher forcing. If we set this to ``True`` then our Model's
            # ``forward()`` method would receive an additional argument called
            # *prev_output_tokens* that would contain a shifted version of the
            # target sequence.
            input_feeding=False,
        )

    def max_positions(self):
        """Return the max input length allowed by the task."""
        # The source should be less than *args.max_positions* and the "target"
        # has max length 1.
        return (self.args.max_positions, 1)

    @property
    def source_dictionary(self):
        """Return the source :class:`~fairseq.data.Dictionary`."""
        return self.input_vocab

    @property
    def target_dictionary(self):
        """Return the target :class:`~fairseq.data.Dictionary`."""
        return self.label_vocab

    # We could override this method if we wanted more control over how batches
    # are constructed, but it's not necessary for this tutorial since we can
    # reuse the batching provided by LanguagePairDataset.
    #
    # def get_batch_iterator(
    #     self, dataset, max_tokens=None, max_sentences=None, max_positions=None,
    #     ignore_invalid_inputs=False, required_batch_size_multiple=1,
    #     seed=1, num_shards=1, shard_id=0, num_workers=0, epoch=1,
    #     data_buffer_size=0, disable_iterator_cache=False,
    # ):
    #     (...)

    def get_batch_iterator(
        self,
        dataset,
        max_tokens=None,
        max_sentences=None,
        max_positions=None,
        ignore_invalid_inputs=False,
        required_batch_size_multiple=1,
        seed=1,
        num_shards=1,
        shard_id=0,
        num_workers=0,
        epoch=1,
        data_buffer_size=0,
        disable_iterator_cache=False,
    ):
        can_reuse_epoch_itr = not disable_iterator_cache and self.can_reuse_epoch_itr(
            dataset
        )
        if can_reuse_epoch_itr and dataset in self.dataset_to_epoch_iter:
            logger.debug("reusing EpochBatchIterator for epoch {}".format(epoch))
            return self.dataset_to_epoch_iter[dataset]

        assert isinstance(dataset, FairseqDataset)

        # initialize the dataset with the correct starting epoch
        dataset.set_epoch(epoch)

        # get indices ordered by example size
        with data_utils.numpy_seed(seed):
            indices = dataset.ordered_indices()

        # filter examples that are too large
        if max_positions is not None:
            indices = self.filter_indices_by_size(
                indices, dataset, max_positions, ignore_invalid_inputs
            )
        logger.info("the max number of tokens are {}".format(max_tokens))
        print("the indices are: {}".format(indices))
        print("the max number of tokens are {}".format(max_tokens))
        print("the number of indices are: {}".format(len(dataset.src_sizes)))
        print(dataset.sizes[indices])
        # create mini-batches with given size constraints
        batch_sampler = dataset.batch_by_size(
            indices,
            max_tokens=max_tokens,
            max_sentences=max_sentences,
            required_batch_size_multiple=required_batch_size_multiple,
        )

        # return a reusable, sharded iterator
        epoch_iter = iterators.EpochBatchIterator(
            dataset=dataset,
            collate_fn=dataset.collater,
            batch_sampler=batch_sampler,
            seed=seed,
            num_shards=num_shards,
            shard_id=shard_id,
            num_workers=num_workers,
            epoch=epoch,
            buffer_size=data_buffer_size,
        )

        if can_reuse_epoch_itr:

            self.dataset_to_epoch_iter[dataset] = epoch_iter

        return epoch_iter